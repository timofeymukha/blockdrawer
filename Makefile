.PHONY: run test integration-test check research-test research-30p30n \
        research-periodic-hill research-cases research-a-airfoil

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

# Every synthetic fixture, including the sharp-feature cavity regression
# case.  A case that cannot be built still writes its report.
research-cases:
	for case in single_ellipse two_circles three_rotated_ellipses \
	            concave_and_convex four_bodies sharp_bodies \
	            narrow_gap_tip peanut_body skimming_tail \
	            straight_channel periodic_hill; do \
	    $(PYTHON) $(RESEARCH)/research_cli.py run --case $$case \
	        --json $(RESEARCH_OUTPUT)/$$case.json \
	        --output $(RESEARCH_OUTPUT)/$$case.png || true; \
	done

# The single-airfoil acceptance case: a C-grid at 15 chords with the wake
# deflected 13 degrees, the band sized from the measured boundary layer.
research-a-airfoil:
	$(PYTHON) $(RESEARCH)/fetch_a_airfoil.py
	$(PYTHON) $(RESEARCH)/research_cli.py run \
		--curve airfoil=$(RESEARCH)/geometry/A-Airfoil-Normalized.dat \
		--farfield-shape cshape --farfield-radius 15 --farfield-center 1,0 \
		--wake --wake-direction 0.9744,0.2250 --layer-height 0.15 \
		--width 700 \
		--output $(RESEARCH_OUTPUT)/a-airfoil.png \
		--json $(RESEARCH_OUTPUT)/a-airfoil.json \
		--session $(RESEARCH_OUTPUT)/a-airfoil-session.json \
		--session-render $(RESEARCH_OUTPUT)/a-airfoil-session.png \
		--block-mesh-dict $(RESEARCH_OUTPUT)/a-airfoil-blockMeshDict

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
