# Algorithms for Constructing Multiblock Mesh Topologies: State of the Art for 2D CFD and Spectral-Element Meshing

## TL;DR
- **The single most production-ready automatic path today is the cross-field / quasi-structured pipeline in Gmsh (open source), while true "block topology" for demanding CFD is still overwhelmingly manual/semi-automatic in ICEM CFD, Fidelity Pointwise, and GridPro** — no commercial tool yet does fully automatic, boundary-layer-aware block decomposition of arbitrary 2D domains at production quality.
- For a 2D CFD/spectral-element multiblock generator, the highest-value research approach to implement is a **cross-field (Ginzburg–Landau / MBO) → separatrix tracing → T-mesh quantization** pipeline, hardened with the robustness ideas of Viertel–Osting–Staten and Reberol et al., and combined with **medial-axis or offset-curve boundary-layer block insertion** for wall alignment.
- The core unsolved problems are identical across all method families: **singularity placement quality, integer/quantization consistency, T-junction elimination, robust handling of thin/concave features and far-field boundaries, and keeping singularities off the wall** so boundary-layer O/C-grids stay structured.

## Key Findings
1. **Three algorithm families dominate automatic 2D block decomposition**: medial-axis transform (MAT), cross/frame fields, and paving/advancing-front. Armstrong's Belfast group has argued these are "alternative views of the same concept" — all ultimately about *placing mesh singularities*.
2. **Cross-field methods are now the research mainstream** and have crossed into production only in Gmsh's quasi-structured quad mesher. They give the best combination of automation, quality, and singularity control, but robustness (limit cycles, singularity misalignment, quantization) remains the pinch point.
3. **Medial-axis methods produce the most flow-aligned meshes for internal CFD.** The Cambridge/Whittle Lab group (Ali, Tucker, Shahpar) found via adjoint error analysis that medial-axis blocking beat Cartesian/H-grid fitting for turbomachinery; in their words, "It is found that, in general, the medial axis based approaches provide optimal blocking and yields better accuracy in computing the functional of interest. This is because the medial axis based methods produce meshes which have better flow alignment especially in case of internal flows." Hand blocking still won in some complex cases.
4. **For spectral-element / high-order specifically**, the Marcon–Kopriva–Sherwin–Peiró "high-resolution PDE" method (Nektar++/NekMesh) is the most directly relevant: it computes a high-order guiding field, traces separatrices with spectral accuracy, and produces *a priori* curved quad blocks needing no a-posteriori curving.
5. **Boundary-layer block insertion** (O-grid/C-grid) is still largely a manual operation in production tools; the most promising automatic analog is offset-curve/extrusion layer insertion (Gmsh "hexbl"/boundary-layer, ICEM O-grid automation, Pointwise T-Rex) combined with keeping singularities off walls.
6. **Polycube and 3D frame-field methods** are powerful for volumetric hex meshing but are 3D-centric, less relevant to the user's 2D priority, and not boundary-layer friendly without special treatment.
7. **Learning-based methods** (RL advancing front, GNN block decomposition) are promising but immature — research prototypes, not yet competitive for CFD boundary-layer quality.

## Details

### 1. Category A — What is already in production

#### 1.1 ANSYS ICEM CFD (Hexa) — manual/semi-automatic blocking with O-grid
ICEM CFD Hexa is the archetypal interactive block-structured mesher. On initialization it creates a single block enclosing the geometry; the user then **splits, merges, and applies "O-grid" operations** (which convert a block into a circular "O"-topology ring) and associates block edges/faces/vertices to CAD curves/surfaces, after which the block model is projected onto the geometry. Block topology is constructed *independent of geometry* and then fitted ("the user acts like a sculptor"). O-grids are the standard device for wrapping a viscous layer around a wall (e.g., an O-grid around a body captures boundary-layer effects); C-H-grid hybrids are built by combining strategies. **Algorithmically it is manual**: there is no published automatic topology-generation algorithm; automation is limited to auto-projection, smoothing (elliptic/Laplacian), and quality (determinant/scaled-Jacobian) checks. Maturity: production. Boundary-layer suitability: excellent, but human-driven. This is verifiable from ANSYS user manuals and documentation.

#### 1.2 Fidelity Pointwise / Gridgen — automatic blocking + T-Rex
Pointwise offers structured, unstructured, and hybrid meshing. Its structured workflow is domain/block-based and largely manual, but it includes an **"automatic blocking"** capability and **T-Rex (anisotropic tetrahedral extrusion)**, which grows anisotropic layers from walls that can be combined/recombined into hex/prism layers for boundary-layer resolution. Public documentation does not disclose the internal block-topology algorithm in full detail; users report medial-axis-assisted workflows are still finished manually (feature blocking via medial axis + TopMaker, far-field with Cartesian/H-blocks, then manual cleanup). *Flag: the precise internal algorithm of Pointwise "automatic blocking" cannot be confirmed from public documentation.*

#### 1.3 GridPro — topology-based structured meshing
GridPro is explicitly topology-first: the user defines a logical blocking ("wireframe") and GridPro's proprietary **Dynamic Boundary Conforming (DBC)** optimization smooths and conforms the grid, optimizing orthogonality. It is semi-automatic (topology reusable across parametric variants). Strong for turbomachinery/periodic geometries. The optimization/smoothing is automatic; topology definition is user-driven. *Flag: DBC internals are proprietary.*

#### 1.4 Coreform Cubit / Cubit (Sandia) — paving, submapping, sweeping
Cubit implements the classic Sandia algorithm suite:
- **Paving** (Blacker & Stephenson 1991): advancing-front all-quad meshing from the boundary inward. Robust, boundary-conforming, but introduces many irregular vertices at front collisions — poor for structured block topology.
- **Submapping** (White; Ruiz-Gironés): decomposes a mappable-ish surface/volume into logical rectangles/cuboids using virtual geometry and integer interval assignment (integer programming for interval matching). Effective only on a restricted class of "blocky" geometry.
- **Sweeping** (2.5D): mesh a source surface, sweep to a target through linking surfaces; requires consistent interval counts along the sweep. Dominant industrial hex method but needs sweepable geometry.
- Cubit also has **boundary-layer meshing designed for fluid flow**. Auto sweep-detection and interval assignment reduce manual decomposition. Maturity: production. Automation: semi (decomposition often manual).

#### 1.5 Gmsh — quasi-structured quad meshing (QuadQS) and boundary layers
Gmsh contains the most advanced *open-source, near-automatic* quad pipeline, the **quasi-structured quadrilateral mesher (QuadQS)** of Reberol, Georgiadis & Remacle ("Quasi-structured quadrilateral meshing in Gmsh – a robust pipeline for complex CAD models," arXiv:2103.04652, submitted 8 March 2021, Université catholique de Louvain; available in Gmsh ≥4.8). In the authors' description, "An initial quad-dominant mesh is generated with frontal point insertion guided by a locally integrable cross field and a scalar size map adapted to the small CAD features... The idea is to preserve the irregular vertices matching cross-field singularities and to eliminate the others." Pipeline in detail: (1) compute a scaled cross field on an initial triangulation using a **fast multilevel heat-diffusion (MBO-style) approach** plus conformal scaling from a linear solve; (2) build a global metric/size field; (3) mesh curves; (4) pattern-mesh simple faces; (5) generate an unstructured quad-dominant mesh by **frontal point insertion guided by the cross field**, then midpoint (Catmull–Clark) subdivision to all-quad; (6) **topological cleanup** — remove irregular vertices that do *not* match cross-field singularities via disk-quadrangulation cavity remeshing, preserving those that do. Robustness is prioritized: every operation is validity/quality-checked (SICN metric) and reverted if it degrades the mesh. The key philosophy is to use the cross field as an *auxiliary guide* rather than relying on it (avoiding the fragility of full integer-grid parametrization). Gmsh also has transfinite (mapped) meshing, recombination (Blossom-Quad perfect matching, and L∞ frontal-Delaunay), and a boundary-layer/`hexbl` branch (see §2.6).

#### 1.6 NekMesh (Nektar++) — high-order curvilinear meshing for spectral elements
NekMesh (Green, Kirilov, Turner, Marcon, Sherwin, Peiró, Moxey; *Computer Physics Communications* 2024) is the reference open-source tool for **high-order curvilinear meshes for spectral/hp element methods**. It uses an *a posteriori* pipeline: generate a linear mesh, then curve it by adding high-order nodes and optimizing (variational / scaled-Jacobian quality). It implements the **isoparametric boundary-layer splitting** of Moxey, Green, Sherwin & Peiró (CMAME 283, 2015) — a macro-element near the wall is split into a graded stack of thin high-order elements via an isoparametric mapping, guaranteeing valid curved boundary-layer elements. NekMesh also implements the Marcon et al. field-guided 2D block decomposition (§2.3). Maturity: production (for high-order); block-topology generation is a newer add-on.

#### 1.7 Nek5000 prenek/genbox, NekRS workflows
Nek5000's native tools (`genbox` for box/tensor-product meshes; `prenek` for interactive 2D quad building and extrusion to hex via `n2to3`) are **manual/scripted**; there is no automatic block topology algorithm. Complex NekRS/Nek5000 meshes are typically built in external tools (Gmsh, Cubit, ICEM) and converted. This confirms the community need the user is targeting.

#### 1.8 OpenFOAM blockMesh / snappyHexMesh, cfMesh
- **blockMesh**: reads a user-written `blockMeshDict` defining hexahedral blocks by vertices, edges, and grading — *fully manual* topology specification. It is a block-structured generator with no automatic decomposition.
- **snappyHexMesh**: starts from a background hex mesh (from blockMesh), then **castellate** (octree refinement + cell removal to conform to an STL), **snap** (morph boundary vertices to the surface), and optionally **add layers** (shrink-and-insert prismatic/hex boundary layers). This is a Cartesian/octree grid-based approach (à la Schneiders) — robust and automatic, but produces hanging-node/split-hex topology with poor near-wall orthogonality and non-conforming refinement, unsuitable for conforming high-order quad/hex blocks.
- **cfMesh**: template/octree Cartesian-based, similar limitations for structured needs.

#### 1.9 Star-CCM+, Salome, others
Star-CCM+ uses automated polyhedral/trimmed-hex (Cartesian) meshing with prism layers — robust, automatic, but not block-structured conforming. Salome offers mapped/quadrangle and various plugins but no automatic block decomposition. Abaqus embeds an augmented Tam–Armstrong medial-axis quad mesher for 2D.

### 2. Category B — Promising research approaches worth implementing

#### 2.1 Medial-axis / medial-object decomposition
**Core idea**: The medial axis (MA) — the locus of centers of maximal inscribed disks — is a natural skeleton. Tam & Armstrong (1991, *Advances in Engineering Software* 13(5–6):313–324) approximate it with a constrained Delaunay triangulation of boundary points (circumcenters approximate the MA), assemble "shape molecules," then insert cuts between medial vertices to reduce the domain to topological 4-, 5-, 6-sided "shape atoms," each meshed by midpoint subdivision (Li, Armstrong & McKeag 1997, *Finite Elem. Anal. Des.* 26(4):279–301). Nackman & Srinivasan first used the MA for decomposition.
- **Dimension**: 2D (Tam–Armstrong, Rigby TopMaker); 3D via medial *surface* subdivision (Price & Armstrong 1995/1997; Fogg et al.). LayTracks3D (Quadros 2014) uses MAT for general solids.
- **Singularities**: MA methods place singularities *far from the boundary* (at medial vertices), which is exactly what boundary-layer CFD wants — regular structured wall layers. This is their key CFD advantage.
- **Robustness / failure modes**: sensitive to small boundary/MA features (spurious branches from Delaunay), and poor handling of concavities and multiple nearby concavities. Fogg, Armstrong & Robinson (CAD 72, 2016, "Enhanced medial-axis-based block-structured meshing in 2-D," 87–101) added use of medial *radii* and *medial angle* plus Bunin's continuum theory to place singularities well and handle concavities.
- **Maturity**: production-adjacent (Rigby's TopMaker 2003/2004; embedded in Abaqus; d-MAT in the Cambridge CFD group). Reference code: not broadly open-source; d-MAT via finite-volume distance field (Xia & Tucker, *IJNME* 82, 2010).
- **CFD suitability**: excellent flow alignment for internal flows; the Ali–Tucker adjoint study (§2.8) ranks it best among automatic methods.
- **Implementation difficulty**: moderate; needs a robust CDT / distance-field solver, MA pruning heuristics.
- **2024/2025 advance**: Lv, Jia, Yan, Armstrong, Robinson, Sun, "Enhanced block-structured quadrilateral mesh generation: integrating cross-field and distance field" (*Engineering with Computers* 41, 2025, 1293–1308) fuses MA (via a boundary-inward distance field on a background triangulation) with a cross field to allow flexible N-sided subdomains, reducing background-mesh dependence.

#### 2.2 Cross-field / frame-field guided block decomposition (the mainstream)
**Core idea**: A *cross field* is a 4-way-rotationally-symmetric direction field (an "infinitesimal quad") whose singularities correspond 1:1 to quad-mesh irregular vertices (Poincaré–Hopf / Abel–Jacobi constraints). Compute a smooth cross field aligned to the boundary; its **singularities** define where block corners go; **tracing separatrices** (streamlines from singularities) partitions the domain into four-sided blocks.

Key methods and lineage:
- **Bommes, Zimmer & Kobbelt, "Mixed-Integer Quadrangulation"** (ACM TOG 28(3), 2009): the seminal cross-field + integer-grid parametrization approach. Code: **libQEx** (quad extraction), CoMISo (mixed-integer solver). Foundational but graphics-oriented; irregular vertices strictly match singularities.
- **Kowalski, Ledoux & Frey**: "A PDE based approach to multidomain partitioning and quadrilateral meshing" (Proc. 21st IMR, 2013, 137–154) and "Automatic domain partitioning for quadrilateral meshing with line constraints" (*Engineering with Computers* 31(3), 2015, 405–421). Solve a diffusion PDE (gradient flow of Dirichlet energy with Lagrange-multiplier unit-norm constraint) for the cross field, then partition by connecting separatrices to singularities. Preserves symmetry, minimizes singularities. Extended to 3D frame fields for hex blocks ("Block-structured hexahedral meshes for CAD models using 3D frame fields," Procedia Eng. 82, 2014). 2D and 3D.
- **Fogg, Armstrong & Robinson, "Automatic generation of multiblock decompositions of surfaces"** (*IJNME* 101(13), 2015, 965–991): cross-field on a triangulation, robust separatrix tracing, singularity placement guided by geometry; handles CFD domains (multi-element aerofoils). 2D surfaces (incl. in 3D space).
- **Beaufort, Lambrechts, Henrotte, Geuzaine & Remacle, "Computing (two dimensional) cross fields — a PDE approach based on the Ginzburg–Landau theory"** (Procedia Eng. 203, 2017, 219–231): formulate the cross field as a unit-norm complex function minimizing the **Ginzburg–Landau functional** (smoothing + penalty), discretized with Crouzeix–Raviart elements. The coherence-length parameter controls singularity placement. This is the theoretically cleanest 2D formulation.
- **Viertel & Osting, "An approach to quad meshing based on harmonic cross-valued maps and the Ginzburg–Landau theory"** (*SIAM J. Sci. Comput.* 41(1), 2019, A452–A479, DOI 10.1137/17M1142703): proves the GL/MBO diffusion-generated method produces cross fields whose separatrices partition into four-sided regions; introduces an efficient **MBO (Merriman–Bence–Osher) diffusion-generated** cross-field solver.
- **Viertel, Osting & Staten, "Coarse quad layouts through robust simplification of cross field separatrix partitions"** (IMR 2019, arXiv:1905.09097): three robustness fixes — high-quality GL/MBO cross fields on curved surfaces, accurate streamline tracing through singular triangles (prevents tangential crossings), and simplification of the naive partition to eliminate small regions and limit cycles. **This is the practical robustness recipe** for a separatrix-based 2D block generator.
- **Reberol et al. QuadQS** (§1.5) — the production embodiment.
- **Jezdimirović, Chemin, Reberol, Henrotte & Remacle, "Quad layouts with high valence singularities for flexible quad meshing"** (Proc. 29th IMR, 2021, arXiv:2103.02939) and "Multi-block decomposition and meshing of 2D domain using Ginzburg–Landau PDE" (Proc. 28th IMR, 2019): compute a cross field *from an imposed singularity set* by solving two linear PDEs, then correct limit cycles / non-quad patches, then generate a block-structured quad mesh per-partition. The 2023 IMR / Springer follow-on "Integrable cross-field generation based on imposed singularity configuration — the 2D manifold case" (LNCSE 147, 2024) adds Abel–Jacobi-valid integrable fields. Gives *control* over singularities — valuable for keeping them off walls.

**Singularities**: intrinsic to the method — placed at cross-field critical points (index ±1/4). Boundary alignment via Dirichlet BCs. Feature curves via line constraints (Kowalski 2015).
**Robustness / failure modes**: limit cycles, singularity misalignment near reentrant corners, small spurious patches, non-integrability of the raw field, and quantization mismatches. These are the central research challenges.
**Maturity**: research → production (Gmsh). **Code**: Gmsh (open source, MBO cross fields, QuadQS); CoMISo/libQEx (MIQ); Viertel MBO (per-paper).
**CFD suitability**: good, but naive cross fields tend to place singularities near walls/concavities — must be constrained; boundary-layer grading needs the conformal scaling / size map. Anisotropic/odeco variants (Couplet, Reberol, Remacle; "Size-controlled quadrilateral meshing using integrable odeco fields," 2025) improve size control.
**Implementation difficulty**: moderate-high; needs a sparse linear/eigen solver and robust streamline tracing; MBO is simpler than mixed-integer.

#### 2.3 High-order PDE guiding field for spectral elements (Marcon–Kopriva–Sherwin–Peiró)
**Core idea** ("A High Resolution PDE Approach to Quadrilateral Mesh Generation," *J. Comput. Phys.* 399, 2019, 108918; arXiv:1901.02405): instead of computing crosses, solve a **Laplace equation for each guiding-field variable using a CG or DG spectral-element method** (in Nektar++), giving spectral convergence and sub-element resolution. Irregular points are located accurately from the high-order field; separatrices are integrated with a **high-order streamline method** that avoids limit cycles. The **DG** formulation lets corners that are not multiples of π/2 be meshed consistently. The resulting curved quad blocks are produced *a priori curved* — no a-posteriori curving needed, ideal for high-order/spectral-element solvers.
- **Dimension**: 2D. **Singularities**: from the guiding field; corners can be smoothed (averaging) instead of forced singular. **Maturity**: research prototype in Nektar++/NekMesh. **Code**: Nektar++/NekMesh (open source). An adaptive version (guiding-field p-adaptation) exists.
- **CFD/spectral suitability**: highest of any research method for the user's exact target (Nek/Nektar-style high-order conforming curved quad blocks). **This should be a primary candidate.**

#### 2.4 Quad-layout / T-mesh / quantization methods (graphics lineage)
These operate on an existing (fine) quad mesh or parametrization to extract a *coarse* conforming quad layout (block structure):
- **Campen, Bommes & Kobbelt, "Dual loops meshing"** (ACM TOG 31(4), 2012) and **Campen & Kobbelt, "Quad layout embedding via aligned parameterization"** (CGF 33(8), 2014, 69–81): build layouts from families of dual loops / aligned parametrization. Survey: **Campen, "Partitioning surfaces into quadrilateral patches: a survey"** (CGF 36(8), 2017, 567–588).
- **Bommes, Campen, Ebke, Alliez & Kobbelt, "Integer-grid maps for reliable quad meshing"** (ACM TOG 32(4), 2013): the rigorous integer-grid map formulation; guarantees valid quad extraction. Code: libQEx.
- **Campen, Bommes & Kobbelt, "Quantized global parametrization"** (ACM TOG 34(6), 2015) and **Lyon, Campen & Kobbelt, "Quad layouts via constrained T-mesh quantization"** (CGF 40(2), 2021, 305–314) and **"Simpler quad layouts using relaxed singularities"** (CGF 40(5), 2021): the **quantization** step — assigning consistent integer edge counts (T-mesh) so opposite block sides match — is the crux; relaxing singularity positions yields coarser, simpler layouts.
- **Razafindrazaka, Reitebuch & Polthier, "Perfect matching quad layouts"** / "Optimal base complexes for quadrilateral meshes" (CAGD 52, 2017, 63–74): graph-matching of singularities into layout edges.
- **Eppstein, Goodrich, Kim & Tamstorf, "Motorcycle graphs: canonical quad mesh partitioning"** (SGP/CGF 2008): trace "motorcycles" from irregular vertices until they hit a prior trace, giving a canonical structured partition (T-mesh). Minimizing block count is NP-hard but approximable. Open-source Python implementation exists (Hassan-Bahrami/MotorCycleGraph); a 2025 "Robust motorcycle graph construction and simplification for semi-structured quad mesh generation" extends it.
- **Dong et al., "Spectral surface quadrangulation"** / Zhang & Bajaj: Morse–Smale complex of Laplacian eigenfunctions gives a quad layout.
- **2025 ACM TOG**: "Field Smoothness-Controlled Partition for Quadrangulation" and "SQuadGen: generating simple quad layouts via chart distance fields" — latest layout-simplification methods.

**Dimension**: mostly surfaces (2D/3D). **Maturity**: research; some open code (libQEx, motorcycle graph). **CFD suitability**: weaker — these optimize for graphics regularity, not wall orthogonality/boundary-layer grading; but the *quantization machinery* is directly reusable in a CFD pipeline. **Difficulty**: high (mixed-integer/quantization solvers).

#### 2.5 Polycube and 3D frame-field hex methods (3D-centric, for context)
- **Polycube**: deform the domain so all boundary normals align to ±axes (a "polycube"), mesh the polycube with a regular grid, map back. Gregson, Sheffer & Zhang, "All-hex mesh generation via volumetric polycube deformation" (CGF 30(5), 2011, 1407–1416); Livesu et al. "PolyCut"; **Dumery, Protais, Mestrallet, Bourcier & Ledoux, "Evocube: a genetic labelling framework for polycube-maps"** (CGF 41, 2022, open-source); Mandad, Chen, Bommes & Campen, "Intrinsic mixed-integer polycubes"; Protais et al. "Robust quantization for polycube maps." The **labelling** problem (assign each boundary face an axis) is the crux; Evocube uses an evolutionary heuristic.
- **3D frame fields**: Ray, Sokolov & Lévy "Practical 3D frame field generation" (ACM TOG 35(6), 2016); Solomon, Vaxman & Bommes "Boundary element octahedral fields in volumes"; Palmer, Bommes & Solomon "Algebraic representations for volumetric frame fields"; Liu et al. "Singularity-constrained octahedral fields for hexahedral meshing." Frame-field singularity graphs are often invalid for hex topology — Armstrong et al. ("Investigating singularities in hex meshing," Springer 2021) use the 3D medial axis to repair them.
- **Surveys**: Pietroni, Campen, Sheffer, Cherchi, Bommes, Gao, Scateni, Ledoux, Remacle, Livesu, "Hex-mesh generation and processing: a survey" (ACM TOG 42, 2023; arXiv:2202.12670); Bommes, Lévy, Pietroni, Puppo, Silva, Tarini & Zorin, "Quad-mesh generation and processing: a survey" (CGF 32(6), 2013, 51–76).

**Relevance to 2D CFD**: low directly, but polycube ≈ 2D "polysquare" and the labelling/quantization ideas inform 2D. **Code**: Evocube, PolyCut, HexaLab (viewer), OpenVolumeMesh, libigl utilities, Gmsh (hexbl). Not boundary-layer friendly without special layers.

#### 2.6 Boundary-layer / CFD-oriented block insertion (critical for the user)
- **Reberol, Verhetsel, Henrotte, Bommes & Remacle, "Robust topological construction of all-hexahedral boundary layer meshes"** (*ACM Trans. Math. Softw.* 2023, DOI 10.1145/3577196; Gmsh `hexbl` branch, open source): builds a topologically optimal all-hex boundary layer on arbitrary ridges/corners via an **integer-programming** formulation (Gecode solver), strictly respecting the input surface, validated on the 114-model MAMBO dataset. The 2D analog — offsetting wall curves to insert boundary-layer quad blocks — is the natural companion to a cross-field interior.
- **Isoparametric boundary-layer splitting** (Moxey, Green, Sherwin & Peiró, CMAME 283, 2015, 636–650): the high-order-valid wall-layer subdivision used in NekMesh.
- **O-grid/C-grid automation**: still mostly manual (ICEM); offset-curve methods and Pointwise T-Rex (anisotropic extrusion → recombined layers) are the automatic analogs.
- **Key principle**: keep cross-field singularities *off* the wall so the near-wall region stays a clean structured O/C-grid; medial-axis methods do this naturally, cross-field methods need constraints.

#### 2.7 Cross-field + medial-axis flow-feature-aligned CFD meshing (newest, 2024–2026)
- **Deng, Wang & Qin, "Cross-Field Structured Adaptive Mesh Using Medial Axis Flow Feature Extraction"** (*AIAA Journal* 62(1), 2024, 247–262, DOI 10.2514/1.J063346): **2D**. In the authors' words, "A method to generate feature-aligned anisotropic structured meshes automatically is presented for high resolution of two-dimensional complicated flow features such as normal and oblique shock waves, shock reflection, shock-shock interaction, and shock-boundary layer interaction... The resulting mesh possesses high element quality for orthogonality and high resolution of the flow features." The loop: (i) generate a multi-block structured grid via the cross-field method (Bunin continuum theory; Fast Marching cross propagation; Bunin index for singularities; separatrix tracing), (ii) solve the flow with an in-house finite-volume RANS code using the **Spalart–Allmaras** turbulence model and the **AUSMPW flux-splitting scheme** "to capture both the shock waves and shear layers accurately," (iii) **extract flow features (shocks) using the medial axis** (Hessian-based feature marking, α-shape boundary recognition, Delaunay circumcenters → medial axis), (iv) embed feature curves as geometric constraints and regenerate feature-aligned anisotropic blocks; iterate ("f-adaptive"). Reported large improvements in shock resolution and cell quality vs. r-/h-adaptation, and converged drag at ~4% of the fully-refined cell count on RAE2822.
- **Wang, Deng, Qin, Cui, Li & Chen, "Flow Feature Aligned Structured Mesh Generation via Sweeping Cross-Field Generated Multiblocks"** (*IJNME* 2026, DOI 10.1002/nme.70388): **3D** extension — generate 2D cross-field cross-sectional meshes, **sweep/extrude** into volumetric hex blocks, embed flow features (shocks/vortices) via a **direction-aware mechanism** (if the feature's sweep direction aligns with the geometry, the feature curve is embedded directly into the cross-sectional block). Targets edge alignment/orthogonality to features (Rankine–Hugoniot across discontinuities).

These show the cross-field + medial-axis lineage is the live frontier for CFD-specific block meshing.

#### 2.8 The Cambridge/Whittle Lab adjoint assessment (which method is best for CFD?)
Ali, Tucker & Shahpar, **"Optimal mesh topology generation for CFD"** (*CMAME* 317, 2017, 431–457, DOI 10.1016/j.cma.2016.12.001) and Ali, Dhanasekaran, Tucker, Watson & Shahpar, **"Optimal multi-block mesh generation for CFD"** (*Int. J. Comput. Fluid Dyn.* 31(4–5), 2017, 195–213): compared **d-MAT (medial axis), TopMaker, Cartesian fitting/H-grid, and manual blocking** on turbomachinery (labyrinth/rim seals, blade passages) using **adjoint-based error analysis** (normalized total error norm, TEN), with multiple flux schemes for scheme-independence. **Finding** (verbatim from the paper): "It is found that, in general, the medial axis based approaches provide optimal blocking and yields better accuracy in computing the functional of interest. This is because the medial axis based methods produce meshes which have better flow alignment especially in case of internal flows. A new hybrid blocking approach, combining the existing methods with the distance field isosurface is also presented to overcome the shortcomings of the current methods." The paper's own highlights describe it as presenting a "level sets and medial axis based novel hybrid blocking method for meshing." Nuance: secondary summaries (Cai et al., *JCDE* 2020) note that "the TEN value of the manual blocking method is the smallest in some cases," so hand blocking still won on some complex geometries. Field verdict, in Armstrong et al.'s words: automatic structured meshing has "been researched for many years without definitive success."

#### 2.9 Learning-based / search-based methods (emerging)
- **Pan, Huang, Cheng & Zeng, "Reinforcement learning for automatic quadrilateral mesh generation: a soft actor-critic approach"** (*Neural Networks* 157, 2023, 288–304; arXiv:2203.11203): formulate advancing-front quad meshing as a Markov decision process; a soft-actor-critic agent extracts one element per step from the evolving front, fully automatic, no cleanup (FreeMesh-RL). Notes mesh generation is one of the NASA CFD Vision 2030 research directions.
- **Tong, Qian, Halilaj & Zhang, "SRL-assisted AFM"** (*J. Comput. Sci.* 72, 2023, 102109): supervised + RL policy networks selecting reference vertices / updating the front; controls extraordinary-point count.
- **Zhou et al.** "Quadrilateral mesh generation method based on convolutional neural network" (*Information* 14(5):273, 2023); GNN-based block decomposition and Monte-Carlo tree search are nascent.
- **PINN-MG** (2025, arXiv:2503.00814): physics-informed neural mesh generation.

**Maturity**: research prototypes; not competitive for CFD boundary-layer quality or guaranteed conformity. **Code**: some (FreeMesh-RL, Evocube). Worth monitoring, not implementing for production 2D CFD yet.

#### 2.10 Other classical approaches
- **Paving/plastering** (Blacker & Stephenson, *IJNME* 32, 1991, 811–847; Staten): advancing front; robust but singularity-heavy.
- **Submapping** (White; Ruiz-Gironés): integer-programming interval assignment on blocky geometry.
- **Grid-based/octree** (Schneiders): Cartesian refinement (snappyHexMesh lineage); poor wall orthogonality.
- **Q-Morph** (Owen et al.): advancing-front triangle-to-quad; **QMorph cross-field driven (QMCF)** (Pellenard et al. 2014).
- **Embedded Voronoi graph** (Sheffer, Etzion, Rappoport, Bercovier) for hex.
- **Bunin's continuum theory** (CAGD 25(1), 2008, 14–40): the theoretical basis linking singularities and mesh distribution used by Fogg and Deng/Wang/Qin.

### 3. Comparative summary table

| Method (family) | Dim | Automation | Robustness | Boundary-layer suitability | Open-source code | Impl. effort |
|---|---|---|---|---|---|---|
| ICEM CFD Hexa blocking + O-grid | 2D/3D | Manual/semi | High (human) | Excellent (manual O/C-grid) | No (commercial) | N/A |
| Pointwise auto-blocking + T-Rex | 2D/3D | Semi | High | Excellent (T-Rex) | No | N/A |
| GridPro (DBC) | 2D/3D | Semi (topology-first) | High | Excellent | No | N/A |
| Cubit paving | 2D/3D | Auto | High | Poor (singularity-heavy) | Partly (Cubit free tiers) | — |
| Cubit submapping/sweeping | 2.5D/3D | Semi | Medium | Good on blocky geom | Partly | — |
| Gmsh QuadQS (cross-field) | 2D/surf | Auto | Medium-high | Medium (needs constraints) | Yes (Gmsh) | Moderate-high |
| Marcon–Kopriva PDE (Nektar++) | 2D | Auto | Medium | Medium-high (curved a priori) | Yes (NekMesh) | Moderate-high |
| Medial axis (Tam–Armstrong, TopMaker, d-MAT) | 2D(3D) | Auto | Medium (concavities) | **Excellent (singularities off wall)** | No (research) | Moderate |
| Cross-field GL/MBO + separatrix (Viertel/Osting) | 2D/surf | Auto | Medium (limit cycles) | Medium | Partial | Moderate |
| Quad-layout quantization (Campen/Lyon/Bommes) | surf | Auto | Medium | Poor (graphics-oriented) | Yes (libQEx/CoMISo) | High |
| Motorcycle graph | quad-mesh | Auto | High (canonical) | N/A (post-process) | Yes (Python/Gmsh) | Low-moderate |
| Polycube (Evocube etc.) | 3D | Auto | Medium | Poor | Yes (Evocube/PolyCut) | High |
| 3D frame fields | 3D | Auto | Low-medium | Poor | Partial | Very high |
| Gmsh hexbl boundary layer | 3D(2D) | Auto | High | **Excellent** | Yes (Gmsh) | High (IP solver) |
| snappyHexMesh (octree) | 3D | Auto | High | Medium (layers, non-conforming) | Yes (OpenFOAM) | — |
| RL / learning-based | 2D | Auto | Low-medium | Poor (not proven) | Partial | High (ML stack) |

### 4. Recommended shortlist (for a 2D CFD spectral-element multiblock generator)

1. **Cross-field (Ginzburg–Landau / MBO) → separatrix tracing → quantized T-mesh, hardened à la Viertel–Osting–Staten and Reberol QuadQS.** This is the best-supported, most automatic path with open reference code (Gmsh). Path: compute an MBO/GL cross field on a triangulation (Gmsh gives this for free), trace separatrices with singular-triangle-accurate integration, simplify to remove limit cycles/small patches, quantize (Lyon T-mesh quantization) so opposite block sides match, then structured-fill each block.
2. **The Marcon–Kopriva–Sherwin–Peiró high-order PDE guiding field in Nektar++/NekMesh.** Uniquely tailored to the user's spectral-element target: produces *a priori curved* conforming quad blocks with spectral-accurate separatrix tracing and DG corner handling. Lowest integration risk into a Nek/Nektar workflow.
3. **Medial-axis decomposition (Fogg-enhanced Tam–Armstrong/TopMaker) as the boundary-layer/feature front end.** Its natural placement of singularities away from walls is exactly what boundary-layer CFD needs; use it to block the near-wall and feature regions, and fill the far-field with H/O blocks. Fuse with the cross field per Lv et al. 2025.
4. **Offset-curve / extrusion boundary-layer block insertion** (2D analog of Gmsh hexbl / ICEM O-grid / Pointwise T-Rex): peel graded wall layers first, keep singularities off the wall, then decompose the interior with methods 1–3. Add NekMesh isoparametric splitting for high-order validity.
5. **(Optional) Motorcycle-graph post-processing** to canonicalize/coarsen the resulting T-mesh into a minimal block layout.

**Suggested implementation path**: (a) triangulate the domain and compute a scaled cross field via MBO in Gmsh; (b) offset wall curves to insert boundary-layer quad blocks and pin singularities to the interior; (c) trace separatrices from interior singularities using high-order/robust integration (Marcon or Viertel); (d) simplify partition (remove limit cycles/tiny patches); (e) quantize edge counts (constrained T-mesh quantization) for conformity; (f) structured-fill each block with graded tensor-product nodes; (g) curve to high order and validate with scaled-Jacobian/SICN; export to Nek5000/NekRS/Nektar++ format.

### 5. Open problems
- **Singularity placement quality** — minimizing count while respecting geometry and keeping singularities off walls; naive GL fields push singularities into coarse/concave regions.
- **Quantization / integer consistency** — assigning matching integer edge counts across block interfaces (mixed-integer/T-mesh quantization) is the hardest reliability bottleneck.
- **T-junction elimination** — converting T-meshes to fully conforming block structures.
- **Robustness on thin/concave geometry** — MA spurious branches, cross-field limit cycles, small patches.
- **Far-field boundaries** for external aerodynamics — cross fields don't naturally produce C/O far-field topology; needs hybrid H/O-block far-field.
- **Mesh grading control** — anisotropic boundary-layer spacing requires conformal scaling / odeco / metric fields.
- **Curved / high-order geometry representation** — a-priori curved blocks (Marcon) vs. a-posteriori curving (NekMesh) and guaranteeing valid high-order Jacobians near walls.

## Recommendations
- **Stage 1 (prototype, ~weeks)**: Use Gmsh's existing MBO cross field + QuadQS as a baseline on your 2D CFD geometries (aerofoils, multi-element sections). Evaluate singularity placement and near-wall behavior directly. Benchmark against a hand-built ICEM/Pointwise block mesh.
- **Stage 2 (boundary layer)**: Implement offset-curve wall-layer block insertion and force singularities into the interior; verify O/C-grid structure near walls and scaled-Jacobian validity after high-order curving (adopt NekMesh isoparametric splitting).
- **Stage 3 (robust interior)**: Implement separatrix tracing with Viertel–Osting–Staten robustness fixes + Lyon-style T-mesh quantization for conforming blocks; or, if you live in Nektar++, adopt the Marcon high-order PDE guiding field directly.
- **Stage 4 (feature alignment, optional)**: Add medial-axis feature/flow-feature extraction (Deng–Wang–Qin style) if you need shock/wake-aligned blocks.
- **Decision thresholds**: If near-wall singularities or limit cycles persist after Stage 2–3, switch the interior front-end to **medial-axis decomposition** (best wall behavior, per the Ali–Tucker adjoint evidence). If quantization proves intractable, fall back to Gmsh's auxiliary-cross-field QuadQS philosophy (unstructured quad + topological cleanup) rather than full integer-grid parametrization. If fully automatic quality never reaches hand-blocking, adopt a semi-automatic tool (GridPro/ICEM) for production and reserve the research generator for parametric sweeps.

## Caveats
- **Commercial tool internals are not fully public.** Claims about Pointwise "automatic blocking," GridPro DBC, and Star-CCM+ internal algorithms are inferred from documentation and cannot be fully verified; treat them as such.
- **"Automatic" rarely means production-ready for CFD boundary layers.** Every fully automatic method reviewed still struggles with wall orthogonality, grading, and singularity control relative to expert manual blocking — a conclusion echoed by the Armstrong and Ali–Tucker groups.
- **Graphics-lineage quad-layout methods optimize for regularity, not CFD.** Their quantization machinery is reusable, but their default objectives ignore boundary-layer and orthogonality needs.
- **Exact quantitative TEN values** from the Ali–Tucker adjoint study could not be extracted from open sources; only the qualitative ranking (medial axis best among automatic; manual sometimes lowest) is firmly documented.
- **Numbers from the Deng–Wang–Qin AIAA paper** are the authors' reported results on their own test cases and flux schemes; independent reproduction is not established.
- **Learning-based results** are on simple/academic domains; none is demonstrated for turbulent-boundary-layer CFD meshes.

### Selected references
- T. Tam & C.G. Armstrong, "2D finite element mesh generation by medial axis subdivision," *Adv. Eng. Softw.* 13(5–6), 1991, 313–324.
- T.S. Li, C.G. Armstrong & R.M. McKeag, "Quad mesh generation for k-sided faces and hex mesh generation for trivalent polyhedra," *Finite Elem. Anal. Des.* 26(4), 1997, 279–301.
- H.J. Fogg, C.G. Armstrong & T.T. Robinson, "Enhanced medial-axis-based block-structured meshing in 2-D," *Computer-Aided Design* 72, 2016, 87–101.
- H.J. Fogg, C.G. Armstrong & T.T. Robinson, "Automatic generation of multiblock decompositions of surfaces," *IJNME* 101(13), 2015, 965–991.
- C.G. Armstrong, H.J. Fogg, C.M. Tierney & T.T. Robinson, "Common themes in multi-block structured quad/hex mesh generation," *Procedia Eng.* 124, 2015, 70–82.
- D. Rigby, "TopMaker: a technique for automatic multi-block topology generation using the medial axis," 2003/2004.
- L. Sun, C.G. Armstrong, T.T. Robinson & D. Papadimitrakis, "Quadrilateral multiblock decomposition via auxiliary subdivision," *J. Comput. Des. Eng.* 8(3), 2021, 871–893.
- Y. Lv, B. Jia, Y. Yan, C.G. Armstrong, T.T. Robinson & L. Sun, "Enhanced block-structured quadrilateral mesh generation: integrating cross-field and distance field," *Engineering with Computers* 41, 2025, 1293–1308.
- N. Kowalski, F. Ledoux & P. Frey, "A PDE based approach to multidomain partitioning and quadrilateral meshing," Proc. 21st IMR, 2013, 137–154.
- N. Kowalski, F. Ledoux & P. Frey, "Automatic domain partitioning for quadrilateral meshing with line constraints," *Engineering with Computers* 31(3), 2015, 405–421.
- D. Bommes, H. Zimmer & L. Kobbelt, "Mixed-integer quadrangulation," *ACM TOG* 28(3), 2009, 77.
- D. Bommes, M. Campen, H.-C. Ebke, P. Alliez & L. Kobbelt, "Integer-grid maps for reliable quad meshing," *ACM TOG* 32(4), 2013.
- P.-A. Beaufort, J. Lambrechts, F. Henrotte, C. Geuzaine & J.-F. Remacle, "Computing cross fields — a PDE approach based on the Ginzburg–Landau theory," *Procedia Eng.* 203, 2017, 219–231.
- R. Viertel & B. Osting, "An approach to quad meshing based on harmonic cross-valued maps and the Ginzburg–Landau theory," *SIAM J. Sci. Comput.* 41(1), 2019, A452–A479, DOI 10.1137/17M1142703.
- R. Viertel, B. Osting & M. Staten, "Coarse quad layouts through robust simplification of cross field separatrix partitions," IMR 2019, arXiv:1905.09097.
- M. Reberol, C. Georgiadis & J.-F. Remacle, "Quasi-structured quadrilateral meshing in Gmsh — a robust pipeline for complex CAD models," arXiv:2103.04652, 2021.
- J. Jezdimirović, A. Chemin, M. Reberol, F. Henrotte & J.-F. Remacle, "Quad layouts with high valence singularities for flexible quad meshing," Proc. 29th IMR, 2021, arXiv:2103.02939.
- J. Marcon, D.A. Kopriva, S.J. Sherwin & J. Peiró, "A high resolution PDE approach to quadrilateral mesh generation," *J. Comput. Phys.* 399, 2019, 108918.
- D. Moxey, M. Green, S.J. Sherwin & J. Peiró, "An isoparametric approach to high-order curvilinear boundary-layer meshing," *CMAME* 283, 2015, 636–650.
- M.D. Green, K.S. Kirilov, M. Turner, J. Marcon, J. Eichstädt, E. Laughton, C.D. Cantwell, S.J. Sherwin, J. Peiró & D. Moxey, "NekMesh: an open-source high-order mesh generation framework," *Comput. Phys. Commun.*, 2024.
- M. Campen, "Partitioning surfaces into quadrilateral patches: a survey," *CGF* 36(8), 2017, 567–588.
- M. Campen, D. Bommes & L. Kobbelt, "Dual loops meshing," *ACM TOG* 31(4), 2012.
- M. Lyon, M. Campen & L. Kobbelt, "Quad layouts via constrained T-mesh quantization," *CGF* 40(2), 2021, 305–314; and "Simpler quad layouts using relaxed singularities," *CGF* 40(5), 2021.
- D. Eppstein, M.T. Goodrich, E. Kim & R. Tamstorf, "Motorcycle graphs: canonical quad mesh partitioning," SGP/CGF, 2008.
- J. Gregson, A. Sheffer & E. Zhang, "All-hex mesh generation via volumetric polycube deformation," *CGF* 30(5), 2011, 1407–1416.
- C. Dumery, F. Protais, S. Mestrallet, C. Bourcier & F. Ledoux, "Evocube: a genetic labelling framework for polycube-maps," *CGF* 41, 2022.
- N. Pietroni, M. Campen, A. Sheffer, et al., "Hex-mesh generation and processing: a survey," *ACM TOG* 42, 2023, arXiv:2202.12670.
- M. Reberol, K. Verhetsel, F. Henrotte, D. Bommes & J.-F. Remacle, "Robust topological construction of all-hexahedral boundary layer meshes," *ACM Trans. Math. Softw.*, 2023, DOI 10.1145/3577196.
- Z. Ali, P.G. Tucker & S. Shahpar, "Optimal mesh topology generation for CFD," *CMAME* 317, 2017, 431–457.
- Z. Ali, P.C. Dhanasekaran, P.G. Tucker, R. Watson & S. Shahpar, "Optimal multi-block mesh generation for CFD," *Int. J. Comput. Fluid Dyn.* 31(4–5), 2017, 195–213.
- S. Deng, Y. Wang & N. Qin, "Cross-field structured adaptive mesh using medial axis flow feature extraction," *AIAA Journal* 62(1), 2024, 247–262, DOI 10.2514/1.J063346.
- Y. Wang, S. Deng, N. Qin, P. Cui, H. Li & B. Chen, "Flow feature aligned structured mesh generation via sweeping cross-field generated multiblocks," *IJNME*, 2026, DOI 10.1002/nme.70388.
- J. Pan, J. Huang, G. Cheng & Y. Zeng, "Reinforcement learning for automatic quadrilateral mesh generation: a soft actor-critic approach," *Neural Networks* 157, 2023, 288–304.
- H. Tong, K. Qian, E. Halilaj & Y.J. Zhang, "SRL-assisted AFM," *J. Comput. Sci.* 72, 2023, 102109.
- T.D. Blacker & M.B. Stephenson, "Paving: a new approach to automated quadrilateral mesh generation," *IJNME* 32, 1991, 811–847.
- G. Bunin, "A continuum theory for unstructured mesh generation in two dimensions," *CAGD* 25(1), 2008, 14–40.