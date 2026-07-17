.PHONY: run test integration-test

PYTHON ?= python3
OPENFOAM_IMAGE ?= /tmp/timofey/code/openfoam-apptainer/openfoam-2606.sif

run:
	$(PYTHON) -m blockdrawer

test:
	$(PYTHON) -m unittest discover -s tests -v

integration-test:
	BLOCKMESH_COMMAND="apptainer exec $(OPENFOAM_IMAGE) bash -lc 'source /usr/lib/openfoam/openfoam2606/etc/bashrc && exec blockMesh \"\$$@\"' blockMesh" \
		$(PYTHON) -m unittest tests.test_openfoam_integration -v
