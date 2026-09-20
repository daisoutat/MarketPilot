"""MarketPilot infra — Phase 0 base.

Deploys: VPC + NAT, Aurora PostgreSQL (serverless v2), reports S3 bucket, ECR,
FastAPI on ECS Fargate + ALB (Cognito-authenticated), worker Lambdas with
schedules/DLQs, alarms + SNS, and CloudWatch self-heal hooks.

Security posture: private subnets for DB and workers, secrets in
Secrets Manager only, least-privilege IAM.
"""
from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    Duration,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_evt,
    aws_logs as logs,
    aws_rds as rds,
    aws_s3 as s3,
    aws_sns as sns,
    aws_sqs as sqs,
    RemovalPolicy,
)
from constructs import Construct


class MarketPilotStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        region: str,
        account: str | None,
        enable_nat: bool,
        self_destruct: bool,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)
        self.region = region
        removal = RemovalPolicy.DESTROY if self_destruct else RemovalPolicy.RETAIN
        self._worker_fns: list[lambda_.Function] = []
        self._worker_dlqs: list[sqs.Queue] = []

        vpc = self._build_vpc(enable_nat)
        reports_bucket = self._reports_bucket(removal)
        cognito_user_pool, cognito_client = self._cognito()
        db = self._database(vpc, removal)
        self._web_service(
            vpc,
            db,
            cognito_user_pool,
            cognito_client,
            removal,
        )
        self._workers(vpc, db, reports_bucket)

        # --- Autonomy hooks -------------------------------------------------
        alarm_topic = sns.Topic(self, "AlarmTopic",
                                topic_name="marketpilot-alarms")
        self._alarms(alarm_topic)

    # ------------------------------------------------------------------ VPC
    def _build_vpc(self, enable_nat: bool) -> ec2.Vpc:
        return ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1 if enable_nat else 0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
            ],
        )

    # ---------------------------------------------------------------- S3
    def _reports_bucket(self, removal: RemovalPolicy) -> s3.Bucket:
        return s3.Bucket(
            self,
            "ReportsBucket",
            bucket_name=f"marketpilot-reports-{self.account}",
            removal_policy=removal,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(prefix="reports/", expired_object_delete_marker=False,
                                 transitions=[s3.Transition(storage_class=s3.StorageClass.INFREQUENT_ACCESS, transition_after=Duration.days(30))]),
            ],
        )

    # ------------------------------------------------------------- Cognito
    def _cognito(self):
        pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="marketpilot-users",
            self_sign_up_enabled=False,
            standard_attributes={
                "email": cognito.StandardAttribute(required=True, mutable=True),
            },
            sign_in_aliases=cognito.SignInAliases(username=True, email=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,
        )
        for group_name in ("admin", "analyst", "viewer"):
            cognito.CfnUserPoolGroup(self, f"Group{group_name}", group_name=group_name, user_pool_id=pool.user_pool_id)
        client = pool.add_client(
            "WebClient",
            user_pool_client_name="marketpilot-web",
            generate_secret=False,
            prevent_user_existence_errors=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
                callback_urls=["http://localhost:5173/callback"],
                logout_urls=["http://localhost:5173"],
            ),
        )
        return pool, client

    # -------------------------------------------------------------- Aurora
    def _database(self, vpc: ec2.Vpc, removal: RemovalPolicy) -> rds.DatabaseCluster:
        db = rds.DatabaseCluster(
            self,
            "Aurora",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_16_1,
            ),
            vpc=vpc,
            writer=rds.ClusterInstance.serverless_v2("Writer"),
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=2.0,
            credentials=rds.Credentials.from_generated_secret("mp"),
            default_database_name="marketpilot",
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            removal_policy=removal,
        )
        self.database = db
        return db

    # --------------------------------------------------- ECS web + Cognito
    def _web_service(
        self,
        vpc: ec2.Vpc,
        db: rds.DatabaseCluster,
        pool: cognito.UserPool,
        client: cognito.UserPoolClient,
        removal: RemovalPolicy,
    ) -> None:
        repo = ecr.Repository(
            self, "ApiRepo", repository_name="marketpilot/api",
            removal_policy=removal,
            auto_delete_images=True,
        )
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)
        task_role = iam.Role(
            self, "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        db.secret.grant_read(task_role)
        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "Web",
            service_name="marketpilot-web",
            cluster=cluster,
            cpu=256,
            memory_limit_mib=512,
            desired_count=1,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=ecs.CpuArchitecture.X86_64,
            ),
            public_load_balancer=True,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_ecr_repository(repo, tag="latest"),
                container_port=8000,
                environment={
                    "MP_APP_ENV": "prod",
                    "MP_AUTH_MODE": "cognito",
                    "MP_COGNITO_REGION": self.region,
                    "MP_COGNITO_USER_POOL_ID": pool.user_pool_id,
                    "MP_COGNITO_CLIENT_ID": client.user_pool_client_id,
                    "MP_DB_SECRET_ARN": db.secret.secret_arn,
                },
            ),
            task_role=task_role,
        )
        service.target_group.configure_health_check(
            path="/healthz",
            healthy_http_codes="200",
        )
        service.target_group.set_attribute("deregistration_delay.timeout_seconds", "30")
        service.node.add_dependency(db)
        self.api_service = service
        self.api_repo = repo
        cdk_cluster_sg = cluster.connections.security_groups
        db.connections.allow_default_port_from(ec2.Peer.ipv4(vpc.vpc_cidr_block))

    # -------------------------------------------------------------- Workers
    def _workers(self, vpc: ec2.Vpc, db: rds.DatabaseCluster, bucket: s3.Bucket) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        worker_code = lambda_.Code.from_asset(
            str(repo_root),
            exclude=[
                "api/**", "web/**", "infra/**", "ci/**", "tests/**", "data/**",
                ".git/**", ".github/**", ".venv/**",
                "workers/layer/**",
                "**/__pycache__/**", "**/*.pyc", "*.log", "*.db", "*.sqlite",
            ],
        )
        env = {
            "MP_MARKETPLACES": "US,CA",
            "MP_DB_SECRET_ARN": db.secret.secret_arn,
            "MP_REPORTS_BUCKET": bucket.bucket_name,
            # PA-API research (Associates). Keys are deployment secrets
            # (MP_PA_ACCESS_KEY / MP_PA_SECRET_KEY); the tag + FX config are
            # non-sensitive defaults, overridable per environment.
            "MP_PA_REGION": "us-east-1",
            "MP_PA_PARTNER_TAG": "",
            "MP_RESEARCH_KEYWORDS": "",
            "MP_FX_BASE_URL": "https://api.frankfurter.app",
            "MP_FX_TTL": "1800",
        }
        sg = ec2.SecurityGroup(self, "WorkerSg", vpc=vpc, description="worker lambdas")
        db.connections.allow_default_port_from(sg)

        # Runtime deps (pip-installed by ci/build-layer.sh into workers/layer/python).
        layer_dir = repo_root / "workers" / "layer"
        layer = lambda_.LayerVersion(
            self, "WorkerLayer",
            code=lambda_.Code.from_asset(str(layer_dir)),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
        )

        def make_lambda(name: str, fn: str, schedule: str, timeout_s: int = 120,
                        extra_env: dict | None = None) -> lambda_.Function:
            dlq = sqs.Queue(
                self, f"{name}Dlq",
                queue_name=f"mp-{fn.replace('_', '-')}-dlq",
            )
            self._worker_dlqs.append(dlq)
            r = lambda_.Function(
                self,
                name,
                function_name=f"mp-{fn.replace('_', '-')}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler=f"workers.funcs.{fn}.handler",
                code=worker_code,
                layers=[layer],
                environment={**env, "MP_JOB": fn, **(extra_env or {})},
                vpc=vpc,
                security_groups=[sg],
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                timeout=Duration.seconds(timeout_s),
                memory_size=256,
                dead_letter_queue=dlq,
                max_retry_attempts=3,
                log_retention=logs.RetentionDays.ONE_MONTH,
            )
            r.role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue", "secretsmanager:GetResourcePolicy"],
                    resources=[db.secret.secret_arn],
                )
            )
            bucket.grant_read_write(r)
            events.Rule(self, f"{name}Rule",
                        schedule=events.Schedule.expression(schedule)).add_target(
                events_targets.LambdaFunction(r)
            )
            self._worker_fns.append(r)
            return r

        make_lambda("OrdersSync", "orders_sync", "rate(10 minutes)")
        make_lambda("ReportsSync", "reports_sync", "cron(0 3 * * ? *)")
        make_lambda("PriceSnapshot", "price_snapshot", "rate(1 hour)")
        make_lambda("AlertsEval", "alerts_eval", "rate(10 minutes)")
        make_lambda("Taxonomy", "taxonomy", "cron(0 4 * * ? *)")
        make_lambda("Forecast", "forecast", "cron(0 5 * * ? *)")
        self_heal = make_lambda(
            "SelfHeal", "self_heal", "rate(5 minutes)", timeout_s=300,
            extra_env={
                "MP_HEAL_SQS": "1",
                "MP_HEAL_STALE": "orders-sync:15,price-snapshot:180,reports-sync:1440,alerts-eval:30,taxonomy:1440,forecast:2880",
                "MP_HEAL_DLQS": "orders_sync,reports_sync,price_snapshot,alerts_eval,webhook_consumer,taxonomy,forecast",
            },
        )

        # Webhook consumer is SQS-triggered, not scheduled.
        queue = sqs.Queue(self, "SpApiWebhook", queue_name="mp-spapi-notifications", visibility_timeout=Duration.seconds(60))
        consumer_dlq = sqs.Queue(
            self, "WebhookConsumerDlq",
            queue_name="mp-webhook-consumer-dlq",
        )
        self._worker_dlqs.append(consumer_dlq)
        consumer = lambda_.Function(
            self,
            "WebhookConsumer",
            function_name="mp-webhook-consumer",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="workers.funcs.webhook_consumer.handler",
            code=worker_code,
            layers=[layer],
            environment={**env, "MP_JOB": "webhook_consumer"},
            vpc=vpc,
            security_groups=[sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            timeout=Duration.seconds(60),
            memory_size=256,
            dead_letter_queue=consumer_dlq,
            max_retry_attempts=3,
            log_retention=logs.RetentionDays.ONE_MONTH,
        )
        consumer.add_event_source(lambda_evt.SqsEventSource(queue, batch_size=5))
        self._worker_fns.append(consumer)

        # Self-heal replays DB-blob messages from every worker DLQ, so it needs
        # read/delete access to them all.
        for dlq in self._worker_dlqs:
            dlq.grant_consume_messages(self_heal)

    # --------------------------------------------------------------- Alarms
    def _alarms(self, topic: sns.Topic) -> None:
        topic.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")],
                resources=["*"],
            )
        )

        def add_alarm(
            constraint,
            *,
            metric,
            threshold,
            eval_periods,
            comparison,
            name,
            desc,
            statistic="Sum",
        ) -> cloudwatch.Alarm:
            alarm = cloudwatch.Alarm(
                self,
                constraint,
                alarm_name=name,
                metric=metric,
                threshold=threshold,
                evaluation_periods=eval_periods,
                statistic=statistic,
                comparison_operator=comparison,
                alarm_description=desc,
                actions_enabled=True,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(topic)
            return alarm

        gte = cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD

        # Lambda error rate — any error in a 5-minute window is actionable for
        # workers that are supposed to be silently green.
        for fn in self._worker_fns:
            fn_name = fn.function_name  # type: ignore[attr-defined]
            add_alarm(
                f"LambdaErrors{fn_name}",
                metric=fn.metric_errors(),
                threshold=0,
                eval_periods=1,
                comparison=gte,
                name=f"mp-{fn_name}-errors",
                desc=f"{fn_name} reported >=1 error in the last evaluation period",
            )

        # DLQ depth — messages waiting for replay is an early-warning signal.
        for i, dlq in enumerate(self._worker_dlqs):
            add_alarm(
                f"DlqDepth{i}",
                metric=dlq.metric_approximate_number_of_messages_visible(),
                threshold=0,
                eval_periods=2,
                comparison=gte,
                name=f"mp-{dlq.queue_name}-depth",
                desc=f"{dlq.queue_name} has visible messages",
            )

        # ECS + Aurora operational alarms.
        service = getattr(self, "api_service", None)
        if service is not None:
            add_alarm(
                "WebCpu",
                metric=service.service.metric_cpu_utilization(),
                threshold=80,
                eval_periods=2,
                comparison=gte,
                name="mp-ecs-cpu",
                desc="marketpilot-web Fargate CPU >= 80% for 2 periods",
                statistic="Average",
            )
            add_alarm(
                "WebUnhealthyHosts",
                metric=cloudwatch.Metric(
                    namespace="AWS/ApplicationELB",
                    metric_name="UnhealthyHostCount",
                    dimensions_map={
                        "LoadBalancer": service.load_balancer.load_balancer_full_name,
                        "TargetGroup": service.target_group.target_group_full_name,
                    },
                ),
                threshold=0,
                eval_periods=2,
                comparison=gte,
                name="mp-ecs-unhealthy-hosts",
                desc="ALB target group has unhealthy host(s)",
            )
        db = getattr(self, "database", None)
        db_cluster_id = db.cluster_identifier if db is not None else "marketpilot"
        add_alarm(
            "AuroraCpu",
            metric=cloudwatch.Metric(
                namespace="AWS/RDS",
                metric_name="CPUUtilization",
                dimensions_map={"DBClusterIdentifier": db_cluster_id},
            ),
            threshold=80,
            eval_periods=2,
            comparison=gte,
            name="mp-aurora-cpu",
            desc="Aurora cluster CPU >= 80% for 2 periods",
            statistic="Average",
        )