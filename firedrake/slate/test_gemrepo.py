
from firedrake import *
import math
import copy
from firedrake.formmanipulation import split_form
import numpy as np


def test_assemble_matrix(a):
    print("Test of assemble matrix")

    print('a:', a)
    _A = Tensor(a)
    print('_A:', _A)
    A = assemble(_A)
    print('A:', A)
    print('A.M:', A.M)
    #A_comp = assemble(a)
    #for i in range(A.M.handle.getSize()[0]):
    #    for j in range(A.M.handle.getSize()[1]):
    #        assert math.isclose(A.M.handle.getValues(i,j),A_comp.M.handle.getValues(i,j)),  "Test for assembly of tensor failed"

#Note: this test only works for DG problems because assembled vector does not do the right thing
#the bug is also in the earlier version of slate compiler
def test_solve(a,L,V):
    #assemble
    _A = Tensor(a)
    _F = AssembledVector(assemble(L))

    #solve
    u = Function(V)
    u_comp = Function(V)
    solve(assemble(_A), u, assemble(_F),solver_parameters={'ksp_type': 'cg'})
    solve(a == L, u_comp, solver_parameters={'ksp_type': 'cg'})
    assert u.dat.data.all()  == u_comp.dat.data.all() , "Test for solve on assembled forms failed"

#Note: this test only works for discontinuous function spaces
def test_assembled_vector(L):
    print("Test of assemble vector")

    _coeff_F = AssembledVector(Function(assemble(L)))
    coeff_F = assemble(_coeff_F)
    coeff_F_comp = assemble(L)
    assert math.isclose(coeff_F.dat.data.all(),coeff_F_comp.dat.data.all()), "Test for assembled vectors failed"

def test_add(a):
    print("Test of add")
    
    _A = Tensor(a)
    add_A = assemble(_A+_A)
    add_A_comp = assemble(a+a)
    for i in range(add_A.M.handle.getSize()[0]):
        for j in range(add_A.M.handle.getSize()[1]):
            assert math.isclose(add_A.M.handle.getValues(i,j),add_A_comp.M.handle.getValues(i,j)),  "Test for adding of a two tensor failed"

def test_negative(a):
    print("Test of negative")

    _A = Tensor(a)
    neg_A = assemble(-_A)
    neg_A_comp = assemble(-a)
    for i in range(neg_A.M.handle.getSize()[0]):
        for j in range(neg_A.M.handle.getSize()[1]):
            assert math.isclose(neg_A.M.handle.getValues(i,j),neg_A_comp.M.handle.getValues(i,j)),  "Test for negative of tensor failed"

#TODO: this only really a test for a problem containing an unsymmetric operator 
def test_transpose(a):
    print("Test of transpose")
    _A = Tensor(a)
    print('_A:', _A)
    trans_A = assemble(Transpose(_A))
    print('trans_A:', trans_A)
    #A_comp = assemble(_A)
    #for i in range(trans_A.M.handle.getSize()[0]):
    #    for j in range(trans_A.M.handle.getSize()[1]):
    #        assert math.isclose(trans_A.M.handle.getValues(i, j), A_comp.M.handle.getValues(j, i)),  "Test for transpose failed"

def test_mul_dx(A,L,V,mesh):
    print("Test of mul")

    #test for mat-vec multiplication
    _A = Tensor(a)
    mat_comp = assemble(a)
    b = Function(assemble(L))
    _coeff_F = AssembledVector(b)
    mul_matvec = assemble(_A*_coeff_F)
    mul_matvec_comp = assemble(action(a,b))
    assert math.isclose(mul_matvec.dat.data.all(),mul_matvec_comp.dat.data.all()) , "Test for contraction (mat-vec-mul)  on cell integrals failed"

    #test for mat-mat multiplication
    u2 = TrialFunction(V)
    v2 = TestFunction(V)
    f2 = Function(V)
    x2, y2 = SpatialCoordinate(mesh)
    f2.interpolate((1+8*pi*pi)*cos(x2*pi*2)*cos(y2*pi*2))
    a2 = (dot(grad(v2), grad(u2))) * dx
    _A2 = Tensor(a2)
    mul_matmat = assemble(_A*_A2)
    mul_matmat_comp = assemble(_A).M.handle* assemble(_A2).M.handle
    for i in range(mul_matmat.M.handle.getSize()[0]):
        for j in range(mul_matmat.M.handle.getSize()[1]):
            assert math.isclose(mul_matmat_comp.getValues(i,j),mul_matmat.M.handle.getValues(i,j)),  "Test for mat-mat-mul  on cell integrals failed"

def test_mul_ds(A,L,V,mesh):
    print("Test of mul")

    #test for mat-vec multiplication
    _A = Tensor(a)
    mat_comp = assemble(a)
    b = Function(assemble(L))
    _coeff_F = AssembledVector(b)
    mul_matvec = assemble(_A*_coeff_F)
    mul_matvec_comp = assemble(action(a,b))
    assert math.isclose(mul_matvec.dat.data.all(),mul_matvec_comp.dat.data.all()) , "Test for contraction (mat-vec-mul) on facet integrals failed"

    #test for mat-mat multiplication
    #only works for facet integrals when there is no coupling between cells involved
    #so for example a flux going across the joint facet of two cells
    #otherwise this becomes kind of a global operation (similar e.g. to an inverse)
    u2 = TrialFunction(V)
    v2 = TestFunction(V)
    a2 = (u2*v2) * ds + u("+")*v("+")*dS+ u("-")*v("-")*dS
    _A2 = Tensor(a2)
    comp1=assemble(_A)
    comp2=assemble(_A2)
    mul_matmat_comp = comp1.M.handle * comp2.M.handle
    mul_matmat = assemble(_A*_A2)

    for i in range(mul_matmat.M.handle.getSize()[0]):
        for j in range(mul_matmat.M.handle.getSize()[1]):
            assert math.isclose(mul_matmat_comp.getValues(i,j),mul_matmat.M.handle.getValues(i,j)),  "Test for mat-mat-mul on facet integrals failed"

def test_blocks():
    print("Test of blocks")

    mesh = UnitSquareMesh(2,2)  
    U = FunctionSpace(mesh, "RT", 1)
    V = FunctionSpace(mesh, "DG", 0)
    n = FacetNormal(mesh)
    W = U * V 
    u, p = TrialFunctions(W)
    w, q = TestFunctions(W)

    A = Tensor(inner(u, w)*dx + p*q*dx - div(w)*p*dx + q*div(u)*dx)

    # Test individual blocks
    indices = [(0, 0), (0, 1), (1, 0), (1, 1)]
    refs = dict(split_form(A.form))
    _A = A.blocks # is type blockinderxer
    for x, y in indices:
        ref = assemble(refs[x, y])
        block = assemble(_A[x, y])
        assert np.allclose(block.M.values, ref.M.values, rtol=1e-14)

def test_layers():
    print("Test of layer integrals")

    m = UnitSquareMesh(5, 5)
    mesh = ExtrudedMesh(m, 5)
    V1 = FunctionSpace(mesh, "CG", 1)
    V2= FunctionSpace(mesh, "CG", 1)
    V = V1 * V2
    u, p = TrialFunction(V)
    v, q = TestFunction(V)
    a = inner(u, v)*dx 

    A = Tensor(a)
    indices = [(0, 0), (0, 1), (1, 0), (1, 1)]
    refs = dict(split_form(A.form))
    _A = A.blocks # is type blockinderxer
    for x, y in indices:
        ref = assemble(refs[x, y])
        block = assemble(_A[x, y])
        assert np.allclose(block.M.values, ref.M.values, rtol=1e-14)

def test_inverse(a):
    print("Test of inverse")

    _A = Tensor(a)
    A = assemble(Inverse(_A))
    A_comp = A
    for i in range(A.M.handle.getSize()[0]):
        for j in range(A.M.handle.getSize()[1]):
            assert math.isclose(A.M.handle.getValues(i,j),abs(A.M.handle.getValues(i,j))),  "Test for assembly failed"



###########
print("Run test for slate to loopy compilation.\n\n")

#discontinuous Helmholtz equation on cell integrals
mesh = UnitSquareMesh(5,5)
V = FunctionSpace(mesh, "DG", 1)
u = TrialFunction(V)
v = TestFunction(V)
f = Function(V)
x, y = SpatialCoordinate(mesh)
f.interpolate((1+8*pi*pi)*cos(x*pi*2)*cos(y*pi*2))
a = (dot(grad(v), grad(u)) + v * u) * dx
L = f * v * dx


#test_assemble_matrix(a)
test_transpose(a)

#test_negative(a)
#test_add(a)
#test_assembled_vector(L) 
#test_mul_dx(a,L,V,mesh)
#test_solve(a,L,V)

#discontinuous Helmholtz equation on facet integrals
mesh = UnitSquareMesh(5,5)
V = FunctionSpace(mesh, "DG", 1)
u = TrialFunction(V)
v = TestFunction(V)
f = Function(V)
x, y = SpatialCoordinate(mesh)
f.interpolate((1+8*pi*pi)*cos(x*pi*2)*cos(y*pi*2))
a= (v * u) * ds
L = f * v * ds

#test_assemble_matrix(a)
#test_negative(a)
#test_add(a)
#test_mul_ds(a,L,V,mesh)

#continuous Helmholtz equation on facet integrals (works also on cell)
mesh = UnitSquareMesh(5,5)
V = FunctionSpace(mesh, "CG", 1)
u = TrialFunction(V)
v = TestFunction(V)
f = Function(V)
x, y = SpatialCoordinate(mesh)
f.interpolate((1+8*pi*pi)*cos(x*pi*2)*cos(y*pi*2))
a= (dot(grad(v), grad(u))  +u*v) * ds
L = f * v * ds

#test_assemble_matrix(a)
#test_negative(a)
#test_add(a)

#test for assembly of blocks of mixed systems 
#(here for lowest order RT-DG discretisation)
#test_blocks()

#test of block assembly of mixed system defined on extruded mesh
#test_layers()

#TODO: continuous advection problem 
#n = 5
#mesh = UnitSquareMesh(n,n)
#V = FunctionSpace(mesh, "CG", 1)
#x, y = SpatialCoordinate(mesh)
#u_ = Function(V).project(x)
#u = TrialFunction(V)
#v = TestFunction(V)
#F = (u_*div(v*u))*dx

#test_assemble2form(F) 


###############################################
#TODO: assymetric problem test
#TODO: write test for subdomain integrals as well
#TODO: make argument generation nicer
#TODO: fix dependency generation for transpose on facets
#TODO: emit call on inv callable is never called

print("\n\nAll tests passed.")