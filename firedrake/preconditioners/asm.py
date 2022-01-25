import abc

from pyop2.mpi import COMM_SELF
from pyop2.datatypes import IntType
from firedrake.preconditioners.base import PCBase
from firedrake.petsc import PETSc
from firedrake.dmhooks import get_function_space
from firedrake.logging import warning
import numpy

try:
    from tinyasm import _tinyasm as tinyasm
    have_tinyasm = True
except ImportError:
    have_tinyasm = False


__all__ = ("ASMPatchPC", "ASMStarPC", "ASMVankaPC", "ASMLinesmoothPC", "ASMExtrudedStarPC")


class ASMPatchPC(PCBase):
    ''' PC for PETSc PCASM

    should implement:
    - :meth:`get_patches`
    '''

    @property
    @abc.abstractmethod
    def _prefix(self):
        "Options prefix for the solver (should end in an underscore)"

    def initialize(self, pc):
        # Get context from pc
        _, P = pc.getOperators()
        dm = pc.getDM()
        self.prefix = pc.getOptionsPrefix() + self._prefix

        # Extract function space and mesh to obtain plex and indexing functions
        V = get_function_space(dm)

        # Obtain patches from user defined funtion
        ises = self.get_patches(V)

        # Create new PC object as ASM type and set index sets for patches
        asmpc = PETSc.PC().create(comm=pc.comm)
        asmpc.incrementTabLevel(1, parent=pc)
        asmpc.setOptionsPrefix(self.prefix + "sub_")
        asmpc.setOperators(*pc.getOperators())

        backend = PETSc.Options().getString(self.prefix + "backend",
                                            default="petscasm").lower()
        # Either use PETSc's ASM PC or use TinyASM (as simple ASM
        # implementation designed to be fast for small block sizes).
        if backend == "petscasm":
            asmpc.setType(asmpc.Type.ASM)
            # Set default solver parameters
            asmpc.setASMType(PETSc.PC.ASMType.BASIC)

            dosort = PETSc.Options().getBool(self.prefix + "sort", default=True)
            asmpc.setASMSortIndices(dosort)
            opts = PETSc.Options(asmpc.getOptionsPrefix())
            if "sub_pc_type" not in opts:
                opts["sub_pc_type"] = "lu"
            if "sub_pc_factor_shift_type" not in opts:
                opts["sub_pc_factor_shift_type"] = "NONE"
            lgmap = V.dof_dset.lgmap
            # Translate to global numbers
            ises = tuple(lgmap.applyIS(iset) for iset in ises)
            asmpc.setASMLocalSubdomains(len(ises), ises)
        elif backend == "tinyasm":
            if not have_tinyasm:
                raise ValueError("To use the TinyASM backend you need to install firedrake with TinyASM (firedrake-update --tinyasm)")

            _, P = asmpc.getOperators()
            lgmap = V.dof_dset.lgmap
            P.setLGMap(rmap=lgmap, cmap=lgmap)

            asmpc.setType("tinyasm")
            # TinyASM wants local numbers, no need to translate
            tinyasm.SetASMLocalSubdomains(
                asmpc, ises,
                [W.dm.getDefaultSF() for W in V],
                [W.value_size for W in V],
                sum(W.value_size * W.dof_dset.total_size for W in V))
            asmpc.setUp()
        else:
            raise ValueError(f"Unknown backend type f{backend}")

        asmpc.setFromOptions()
        self.asmpc = asmpc

    @abc.abstractmethod
    def get_patches(self, V):
        ''' Get the patches used for PETSc PSASM

        :param  V: the :class:`~.FunctionSpace`.

        :returns: a list of index sets defining the ASM patches in local
            numbering (before lgmap.apply has been called).
        '''
        pass

    def view(self, pc, viewer=None):
        self.asmpc.view(viewer=viewer)

    def update(self, pc):
        self.asmpc.setUp()

    def apply(self, pc, x, y):
        self.asmpc.apply(x, y)

    def applyTranspose(self, pc, x, y):
        self.asmpc.applyTranspose(x, y)


class ASMStarPC(ASMPatchPC):
    '''Patch-based PC using Star of mesh entities implmented as an
    :class:`ASMPatchPC`.

    ASMStarPC is an additive Schwarz preconditioner where each patch
    consists of all DoFs on the topological star of the mesh entity
    specified by `pc_star_construct_dim`.
    '''

    _prefix = "pc_star_"

    def get_patches(self, V):
        mesh = V._mesh
        mesh_dm = mesh.topology_dm
        if mesh.cell_set._extruded:
            warning("applying ASMStarPC on an extruded mesh")

        # Obtain the topological entities to use to construct the stars
        depth = PETSc.Options().getInt(self.prefix+"construct_dim", default=0)

        # Accessing .indices causes the allocation of a global array,
        # so we need to cache these for efficiency
        V_local_ises_indices = []
        for (i, W) in enumerate(V):
            V_local_ises_indices.append(V.dof_dset.local_ises[i].indices)

        # Build index sets for the patches
        ises = []
        (start, end) = mesh_dm.getDepthStratum(depth)
        for seed in range(start, end):
            # Only build patches over owned DoFs
            if mesh_dm.getLabelValue("pyop2_ghost", seed) != -1:
                continue

            # Create point list from mesh DM
            pt_array, _ = mesh_dm.getTransitiveClosure(seed, useCone=False)
            pt_array = nested_dissection(mesh_dm, pt_array)

            # Get DoF indices for patch
            indices = []
            for (i, W) in enumerate(V):
                section = W.dm.getDefaultSection()
                for p in pt_array.tolist():
                    dof = section.getDof(p)
                    if dof <= 0:
                        continue
                    off = section.getOffset(p)
                    # Local indices within W
                    W_indices = numpy.arange(off*W.value_size, W.value_size * (off + dof), dtype=IntType)
                    indices.extend(V_local_ises_indices[i][W_indices])
            iset = PETSc.IS().createGeneral(indices, comm=COMM_SELF)
            ises.append(iset)

        return ises


class ASMVankaPC(ASMPatchPC):
    '''Patch-based PC using closure of star of mesh entities implmented as an
    :class:`ASMPatchPC`.

    ASMVankaPC is an additive Schwarz preconditioner where each patch
    consists of all DoFs on the closure of the star of the mesh entity
    specified by `pc_vanka_construct_dim` (or codim).
    '''

    _prefix = "pc_vanka_"

    def get_patches(self, V):
        mesh = V._mesh
        mesh_dm = mesh.topology_dm
        if mesh.layers:
            warning("applying ASMVankaPC on an extruded mesh")

        # Obtain the topological entities to use to construct the stars
        depth = PETSc.Options().getInt(self.prefix + "construct_dim", default=-1)
        height = PETSc.Options().getInt(self.prefix + "construct_codim", default=-1)
        if (depth == -1 and height == -1) or (depth != -1 and height != -1):
            raise ValueError(f"Must set exactly one of {self.prefix}construct_dim or {self.prefix}construct_codim")

        # Accessing .indices causes the allocation of a global array,
        # so we need to cache these for efficiency
        V_local_ises_indices = []
        for (i, W) in enumerate(V):
            V_local_ises_indices.append(V.dof_dset.local_ises[i].indices)

        # Build index sets for the patches
        ises = []
        if depth != -1:
            (start, end) = mesh_dm.getDepthStratum(depth)
        else:
            (start, end) = mesh_dm.getHeightStratum(height)

        for seed in range(start, end):
            # Only build patches over owned DoFs
            if mesh_dm.getLabelValue("pyop2_ghost", seed) != -1:
                continue

            # Create point list from mesh DM
            star, _ = mesh_dm.getTransitiveClosure(seed, useCone=False)
            pt_array = set()
            for pt in star.tolist():
                closure, _ = mesh_dm.getTransitiveClosure(seed, useCone=True)
                pt_array.update(closure.tolist())

            # Get DoF indices for patch
            indices = []
            for (i, W) in enumerate(V):
                section = W.dm.getDefaultSection()
                for p in pt_array:
                    dof = section.getDof(p)
                    if dof <= 0:
                        continue
                    off = section.getOffset(p)
                    # Local indices within W
                    W_indices = numpy.arange(off*W.value_size, W.value_size * (off + dof), dtype=IntType)
                    indices.extend(V_local_ises_indices[i][W_indices])
            iset = PETSc.IS().createGeneral(indices, comm=COMM_SELF)
            ises.append(iset)

        return ises


class ASMLinesmoothPC(ASMPatchPC):
    '''Linesmoother PC for extruded meshes implemented as an
    :class:`ASMPatchPC`.

    ASMLinesmoothPC is an additive Schwarz preconditioner where each
    patch consists of all dofs associated with a vertical column (and
    hence extruded meshes are necessary). Three types of columns are
    possible: columns of horizontal faces (each column built over a
    face of the base mesh), columns of vertical faces (each column
    built over an edge of the base mesh), and columns of vertical
    edges (each column built over a vertex of the base mesh).

    To select the column type or types for the patches, use
    'pc_linesmooth_codims' to set integers giving the codimension of
    the base mesh entities for the columns. For example,
    'pc_linesmooth_codims 0,1' creates patches for each cell and each
    facet of the base mesh.
    '''

    _prefix = "pc_linesmooth_"

    def get_patches(self, V):
        mesh = V._mesh
        assert mesh.cell_set._extruded
        dm = mesh.topology_dm
        section = V.dm.getDefaultSection()
        # Obtain the codimensions to loop over from options, if present
        codim_list = PETSc.Options().getString(self.prefix+"codims", "0, 1")
        codim_list = [int(ii) for ii in codim_list.split(",")]

        # Build index sets for the patches
        ises = []
        for codim in codim_list:
            for p in range(*dm.getHeightStratum(codim)):
                # Only want to build patches over owned faces
                if dm.getLabelValue("pyop2_ghost", p) != -1:
                    continue
                dof = section.getDof(p)
                if dof <= 0:
                    continue
                off = section.getOffset(p)
                indices = numpy.arange(off*V.value_size, V.value_size * (off + dof), dtype=IntType)
                iset = PETSc.IS().createGeneral(indices, comm=COMM_SELF)
                ises.append(iset)

        return ises


def nested_dissection(mesh_dm, pt_array):
    num_pt = len(pt_array)
    subgraph = [numpy.intersect1d(pt_array, mesh_dm.getAdjacency(p), return_indices=True)[1] for p in pt_array]
    cols = numpy.concatenate(subgraph).astype(PETSc.IntType)
    rows = numpy.cumsum([0] + [len(neigh) for neigh in subgraph]).astype(PETSc.IntType)
    G = PETSc.Mat().createAIJ((num_pt, num_pt), csr=(rows, cols, numpy.ones(cols.shape)), comm=COMM_SELF)
    rperm, _ = G.getOrdering(PETSc.Mat.OrderingType.ND)
    idx = rperm.getIndices()
    return pt_array[idx]


def get_basemesh_nodes(W):
    pstart, pend = W.mesh().topology_dm.getChart()
    section = W.dm.getDefaultSection()
    # location of first dof on an entity
    basemeshoff = numpy.empty(pend - pstart, dtype=IntType)
    # number of dofs on this entity
    basemeshdof = numpy.empty(pend - pstart, dtype=IntType)
    # number of dofs stacked on this entity in each cell
    basemeshlayeroffset = numpy.empty(pend - pstart, dtype=IntType)

    # For every base mesh entity, what's the layer offset?
    layer_offsets = numpy.full(W.node_set.total_size, -1, dtype=IntType)
    layer_offsets[W.cell_node_map().values_with_halo] = W.cell_node_map().offset
    nlayers = W.mesh().layers

    for p in range(pstart, pend):
        dof = section.getDof(p)
        off = section.getOffset(p)
        if dof == 0:
            dof_per_layer = 0
            layer_offset = 0
        else:
            layer_offset = layer_offsets[off]
            assert layer_offset >= 0
            dof_per_layer = dof - (nlayers - 1) * layer_offset

        basemeshoff[p - pstart] = off
        basemeshdof[p - pstart] = dof_per_layer
        basemeshlayeroffset[p - pstart] = layer_offset

    return basemeshoff, basemeshdof, basemeshlayeroffset


class ASMExtrudedStarPC(ASMStarPC):
    '''Patch-based PC using Star of mesh entities implmented as an
    :class:`ASMPatchPC`.

    ASMExtrudedStarPC is an additive Schwarz preconditioner where each patch
    consists of all DoFs on the topological star of the mesh entity
    specified by `pc_star_construct_dim`.
    '''

    _prefix = 'pc_star_'

    def get_patches(self, V):
        mesh = V.mesh()
        mesh_dm = mesh.topology_dm
        nlayers = mesh.layers
        if not mesh.cell_set._extruded:
            return super(ASMExtrudedStarPC, self).get_patches(V)

        # Obtain the topological entities to use to construct the stars
        depth = PETSc.Options().getInt(self.prefix+"construct_dim",
                                       default=0)

        # Accessing .indices causes the allocation of a global array,
        # so we need to cache these for efficiency
        V_ises = tuple(iset.indices for iset in V.dof_dset.local_ises)
        basemeshoff = []
        basemeshdof = []
        basemeshlayeroffsets = []
        for (i, W) in enumerate(V):
            boff, bdof, blayer_offsets = get_basemesh_nodes(W)
            basemeshoff.append(boff)
            basemeshdof.append(bdof)
            basemeshlayeroffsets.append(blayer_offsets)

        # Build index sets for the patches
        ises = []
        start, end = mesh_dm.getDepthStratum(depth)
        pstart, _ = mesh_dm.getChart()
        for seed in range(start, end):
            # Only build patches over owned DoFs
            if mesh_dm.getLabelValue("pyop2_ghost", seed) != -1:
                continue

            # Create point list from mesh DM
            points, _ = mesh_dm.getTransitiveClosure(seed, useCone=False)
            points = nested_dissection(mesh_dm, points)
            points -= pstart  # offset by chart start
            for k in range(nlayers):
                if k == 0:
                    planes = [1, 0]
                elif k == nlayers - 1:
                    planes = [-1, 0]
                else:
                    planes = [-1, 1, 0]

                indices = []
                # Get DoF indices for patch
                for i, W in enumerate(V):
                    iset = V_ises[i]
                    sdof = []
                    slices = []
                    for plane in planes:
                        for p in points:
                            # How to walk up one layer
                            blayer_offset = basemeshlayeroffsets[i][p]
                            if blayer_offset <= 0:
                                # In this case we don't have any dofs on
                                # this entity.
                                continue
                            # Offset in the global array for the bottom of
                            # the column
                            off = basemeshoff[i][p]
                            # Number of dofs in the interior of the
                            # vertical interval cell on top of this base
                            # entity
                            dof = basemeshdof[i][p]
                            # Hard-code taking the star
                            if plane == 0:
                                begin = off + k * blayer_offset
                                end = off + k * blayer_offset + dof
                            else:
                                begin = off + min(k, k+plane) * blayer_offset + dof
                                end = off + max(k, k+plane) * blayer_offset
                            zlice = slice(W.value_size * begin,
                                          W.value_size * end)
                            slices.append(zlice)
                            sdof.append(end-begin)
                            #indices.extend(iset[zlice])

                    perm = numpy.argsort(numpy.array(sdof), kind='mergesort')
                    #print(list(reversed(perm)))
                    for k in reversed(perm):
                        indices.extend(iset[slices[k]])
                iset = PETSc.IS().createGeneral(indices, comm=COMM_SELF)
                ises.append(iset)
        return ises
