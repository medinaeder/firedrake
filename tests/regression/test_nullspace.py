from firedrake import *
import pytest
import numpy as np


@pytest.fixture(scope='module', params=[False, True])
def V(request):
    quadrilateral = request.param
    m = UnitSquareMesh(25, 25, quadrilateral=quadrilateral)
    return FunctionSpace(m, 'CG', 1)


def test_nullspace(V):
    u = TrialFunction(V)
    v = TestFunction(V)

    a = inner(grad(u), grad(v))*dx
    L = -v*ds(3) + v*ds(4)

    nullspace = VectorSpaceBasis(constant=True)
    u = Function(V)
    solve(a == L, u, nullspace=nullspace)

    exact = Function(V)
    exact.interpolate(Expression('x[1] - 0.5'))
    assert sqrt(assemble((u - exact)*(u - exact)*dx)) < 5e-8


def test_transpose_nullspace():
    errors = []
    for n in range(4, 10):
        mesh = UnitIntervalMesh(2**n)
        V = FunctionSpace(mesh, "CG", 1)
        u = TrialFunction(V)
        v = TestFunction(V)

        a = inner(grad(u), grad(v))*dx
        L = v*dx

        nullspace = VectorSpaceBasis(constant=True)
        u = Function(V)
        u.interpolate(SpatialCoordinate(mesh)[0])
        # Solver diverges with indefinite PC if we don't remove
        # transpose nullspace.
        solve(a == L, u, nullspace=nullspace,
              transpose_nullspace=nullspace,
              solver_parameters={"ksp_type": "cg",
                                 "ksp_initial_guess_non_zero": True,
                                 "pc_type": "gamg"})
        # Solution should integrate to 0.5
        errors.append(assemble(u*dx) - 0.5)
    errors = np.asarray(errors)
    rate = np.log2(errors[:-1] / errors[1:])
    assert (rate > 1.9).all()


def test_nullspace_preassembled(V):
    u = TrialFunction(V)
    v = TestFunction(V)

    a = inner(grad(u), grad(v))*dx
    L = -v*ds(3) + v*ds(4)

    nullspace = VectorSpaceBasis(constant=True)
    u = Function(V)
    A = assemble(a)
    b = assemble(L)
    solve(A, u, b, nullspace=nullspace)

    exact = Function(V)
    exact.interpolate(Expression('x[1] - 0.5'))
    assert sqrt(assemble((u - exact)*(u - exact)*dx)) < 5e-8


def test_nullspace_mixed():
    m = UnitSquareMesh(5, 5)
    BDM = FunctionSpace(m, 'BDM', 1)
    DG = FunctionSpace(m, 'DG', 0)
    W = BDM * DG

    sigma, u = TrialFunctions(W)
    tau, v = TestFunctions(W)

    a = (dot(sigma, tau) + div(tau)*u + div(sigma)*v)*dx

    bcs = [DirichletBC(W.sub(0), (0, 0), (1, 2)),
           DirichletBC(W.sub(0), (0, 1), (3, 4))]

    w = Function(W)

    f = Function(DG)
    f.assign(0)
    L = f*v*dx

    # Null space is constant functions in DG and empty in BDM.
    nullspace = MixedVectorSpaceBasis(W, [W.sub(0), VectorSpaceBasis(constant=True)])

    solve(a == L, w, bcs=bcs, nullspace=nullspace)

    exact = Function(DG)
    exact.interpolate(Expression('x[1] - 0.5'))

    sigma, u = w.split()
    assert sqrt(assemble((u - exact)*(u - exact)*dx)) < 1e-7

    # Now using a Schur complement
    w.assign(0)
    solve(a == L, w, bcs=bcs, nullspace=nullspace,
          solver_parameters={'pc_type': 'fieldsplit',
                             'pc_fieldsplit_type': 'schur',
                             'ksp_type': 'cg',
                             'pc_fieldsplit_schur_fact_type': 'full',
                             'fieldsplit_0_ksp_type': 'preonly',
                             'fieldsplit_0_pc_type': 'lu',
                             'fieldsplit_1_ksp_type': 'cg',
                             'fieldsplit_1_pc_type': 'none'})

    sigma, u = w.split()
    assert sqrt(assemble((u - exact)*(u - exact)*dx)) < 5e-8


def test_near_nullspace():
    mesh = UnitSquareMesh(100, 100)
    dim = 2
    parameters["pyop2_options"]["block_sparsity"] = False
    V = VectorFunctionSpace(mesh, "Lagrange", 1)
    u = TrialFunction(V)
    v = TestFunction(V)

    mu = Constant(0.2)
    lmbda = Constant(0.3)

    def sigma(fn):
        return 2.0 * mu * sym(grad(fn)) + lmbda * tr(sym(grad(fn))) * Identity(dim)

    w_exact = Function(V)
    w_exact.interpolate(Expression(("x[0]*x[1]", "x[0]*x[1]")))
    # div(sigma(w_exact)) = (mu + lmbda, mu + lmbda)
    f = Constant((mu + lmbda, mu + lmbda))
    F = inner(sigma(u), grad(v))*dx + inner(f, v)*dx

    bcs = [DirichletBC(V, w_exact, (1, 2, 3, 4))]

    n0 = Constant((1, 0))
    n1 = Constant((0, 1))
    n2 = Expression(("x[1] - 0.5", "-(x[0]-0.5)"))
    ns = [n0, n1, n2]
    n_interp = [interpolate(n, V) for n in ns]
    n_normalized = [interpolate(n*(1.0/sqrt(n.dat.inner(n.dat))), V) for n in n_interp]
    nsp = VectorSpaceBasis(vecs=n_normalized)

    w = Function(V)
    solve(lhs(F) == rhs(F), w, bcs=bcs, solver_parameters={'ksp_monitor': True, 'ksp_rtol': 1e-8, 'ksp_atol': 1e-8, 'ksp_type': 'cg', 'pc_type': 'gamg'}, near_nullspace=nsp)
    w = Function(V)
    solve(lhs(F) == rhs(F), w, bcs=bcs, solver_parameters={'ksp_monitor': True, 'ksp_rtol': 1e-8, 'ksp_atol': 1e-8, 'ksp_type': 'cg', 'pc_type': 'gamg'})
    assert sqrt(assemble(inner(w-w_exact, w-w_exact)*dx)) < 1e-7


if __name__ == '__main__':
    import os
    pytest.main(os.path.abspath(__file__))
