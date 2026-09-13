.PHONY: run test integration-test check research-test research-30p30n \n        research-periodic-hill

PYTHON ?= python3
OPENFOAM_IMAGE ?= /tmp/timofey/code/openfoam-apptainer/openfoam-2606.sif
RESEARCH ?= experiments/agentic_topology
RESEARCH_OUTPUT ?= $(RESEARCH)/output

run:
	$(PYTHON) -m blockdrawer

test:
	$(PYTHON) -m unittest discover -s tests -v

integration-test:
	BLOCKMESH_COMMAND="apptainer exec $(OPENFOAM_IMAGE) bash -lc 'source /usr/lib/openfoam/openfoam2606/etc/bashrc && exec blockMesh \"\$$@\"' blockMesh" \
		$(PYTHON) -m unittest tests.test_openfoam_integration -v

# Research prototype (NumPy and Pillow only; not part of the runtime package).
research-test:
	$(PYTHON) -m unittest discover -s $(RESEARCH) -v

research-periodic-hill:
	$(PYTHON) $(RESEARCH)/research_cli.py run \
		--case periodic_hill \
		--output $(RESEARCH_OUTPUT)/periodic-hill.png \
		--json $(RESEARCH_OUTPUT)/periodic-hill.json \
		--session $(RESEARCH_OUTPUT)/periodic-hill-session.json \
		--session-render $(RESEARCH_OUTPUT)/periodic-hill-session.png \
		--block-mesh-dict $(RESEARCH_OUTPUT)/periodic-hill-blockMeshDict

research-30p30n:
	$(PYTHON) $(RESEARCH)/fetch_30p30n.py
	$(PYTHON) $(RESEARCH)/research_cli.py run \
		--curve slat=$(RESEARCH)/geometry/30P-30N-Slat-Normalized.dat \
		--curve main=$(RESEARCH)/geometry/30P-30N-Main-Normalized.dat \
		--curve flap=$(RESEARCH)/geometry/30P-30N-Flap-Normalized.dat \
		--width 700 \
		--output $(RESEARCH_OUTPUT)/30p30n.png \
		--json $(RESEARCH_OUTPUT)/30p30n.json \
		--session $(RESEARCH_OUTPUT)/30p30n-session.json \
		--session-render $(RESEARCH_OUTPUT)/30p30n-session.png \
		--block-mesh-dict $(RESEARCH_OUTPUT)/blockMeshDict

# Run blockMesh and checkMesh on a session through the supplied container:
#   make check SESSION=path/to/session.json
check:
	BLOCKMESH_COMMAND="apptainer exec $(OPENFOAM_IMAGE) bash -lc 'source /usr/lib/openfoam/openfoam2606/etc/bashrc && exec blockMesh \"\$$@\"' blockMesh" \
		$(PYTHON) -m blockdrawer.cli check $(SESSION)
