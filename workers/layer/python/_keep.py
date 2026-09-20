# Placeholder so the source tree exists pre-CI. The Lambda layer is built by
# `ci/build-layer.sh` (pip install -t python -r workers/requirements.txt).
# Layer naming scheme: zip root contains a `python/` directory that Lambda
# merges into /opt/python (on sys.path).
SLOT = "populated-by-ci/build-layer.sh with worker runtime deps"