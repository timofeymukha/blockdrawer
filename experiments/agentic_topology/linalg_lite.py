"""Small self-contained linear solvers.

The prototype deliberately avoids ``numpy.linalg`` factorisations and
matrix-matrix products: both bind LAPACK/BLAS builds that are not always
present or healthy in a research environment.  Every system solved here is
either tiny or a symmetric positive-definite graph Laplacian, so plain
elimination and a conjugate-gradient iteration written against elementwise
array primitives are enough.
"""

from __future__ import annotations

import numpy as np


def gram(matrix: np.ndarray) -> np.ndarray:
    """``matrix.T @ matrix`` without a BLAS level-3 call."""
    return np.einsum("ki,kj->ij", matrix, matrix)


def transpose_apply(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """``matrix.T @ vector`` without a BLAS level-3 call."""
    return np.einsum("ki,k->i", matrix, vector)


def dot(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.sum(first * second))


def solve_dense(matrix, vector, *, ridge: float = 0.0) -> np.ndarray:
    """Gaussian elimination with partial pivoting for small dense systems."""
    a = np.array(matrix, dtype=np.float64, copy=True)
    b = np.array(vector, dtype=np.float64, copy=True)
    size = a.shape[0]
    if a.shape != (size, size) or b.shape != (size,):
        raise ValueError("solve_dense needs a square system")
    if ridge:
        a[np.diag_indices(size)] += ridge
    for column in range(size):
        pivot = column + int(np.argmax(np.abs(a[column:, column])))
        if abs(a[pivot, column]) <= 0.0:
            raise ZeroDivisionError("singular system")
        if pivot != column:
            a[[column, pivot]] = a[[pivot, column]]
            b[[column, pivot]] = b[[pivot, column]]
        factor = a[column + 1 :, column] / a[column, column]
        a[column + 1 :, column:] -= factor[:, None] * a[column, column:][None, :]
        b[column + 1 :] -= factor * b[column]
    result = np.zeros(size)
    for column in range(size - 1, -1, -1):
        result[column] = (
            b[column] - dot(a[column, column + 1 :], result[column + 1 :])
        ) / a[column, column]
    return result


def solve_least_squares(matrix, vector, *, ridge: float = 1e-12) -> np.ndarray:
    """Normal-equation least squares with a relative Tikhonov floor."""
    a = np.asarray(matrix, dtype=np.float64)
    b = np.asarray(vector, dtype=np.float64)
    normal = gram(a)
    scale = float(np.trace(normal)) / max(1, normal.shape[0])
    return solve_dense(normal, transpose_apply(a, b), ridge=ridge * max(scale, 1.0))


def solve_laplacian(
    rows: np.ndarray,
    columns: np.ndarray,
    weights: np.ndarray,
    values: np.ndarray,
    fixed: np.ndarray,
    *,
    tolerance: float = 1e-12,
    max_iterations: int = 5000,
) -> np.ndarray:
    """Conjugate-gradient solve of a weighted graph Laplacian.

    ``rows``/``columns``/``weights`` list every directed neighbour relation with
    a positive weight, ``values`` carries the Dirichlet data and the initial
    guess, and ``fixed`` marks the constrained rows.  The free sub-matrix is
    symmetric positive definite whenever the weights are positive and every free
    component touches a constrained row.
    """
    size = len(values)
    total = np.bincount(rows, weights=weights, minlength=size)
    free = ~fixed
    boundary = np.where(fixed, values, 0.0)

    def apply(vector: np.ndarray) -> np.ndarray:
        product = total * vector - np.bincount(
            rows, weights=weights * vector[columns], minlength=size
        )
        product[fixed] = 0.0
        return product

    right = -apply(boundary)
    solution = np.where(free, values, 0.0)
    residual = right - apply(solution)
    direction = residual.copy()
    square = dot(residual, residual)
    target = max(tolerance * max(dot(right, right), 1e-300), 1e-300)
    for _ in range(max_iterations):
        if square <= target:
            break
        product = apply(direction)
        denominator = dot(direction, product)
        if denominator <= 0.0:
            break
        step = square / denominator
        solution += step * direction
        residual -= step * product
        updated = dot(residual, residual)
        direction = residual + (updated / square) * direction
        square = updated
    return solution + boundary
