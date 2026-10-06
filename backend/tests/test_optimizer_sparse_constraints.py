"""Guard constraint semantics when reusing sparse matrices across solves."""

import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csc_array

from optimizer.milp import ConstraintBuilder


def _coo_reference(cb):
    # Preserve the original coordinate compilation as an independent reference.
    row_indices, col_indices, values, lower, upper = [], [], [], [], []
    for row_index, (coefficients, lb, ub) in enumerate(cb.rows):
        for column_index, value in coefficients.items():
            if value:
                row_indices.append(row_index)
                col_indices.append(column_index)
                values.append(value)
        lower.append(lb)
        upper.append(ub)
    matrix = csc_array((values, (row_indices, col_indices)), shape=(len(cb.rows), cb.n), dtype=float)
    return LinearConstraint(matrix, np.array(lower, dtype=float), np.array(upper, dtype=float))


def _compiled_bytes(constraints):
    return constraints.A.shape, tuple(
        (array.dtype, array.tobytes())
        for array in (constraints.A.data, constraints.A.indices, constraints.A.indptr, constraints.lb, constraints.ub)
    )


@pytest.mark.parametrize("columns", [1, 7, 31])
def test_compressed_rows_preserve_coordinate_compilation_bytes(columns):
    cb = ConstraintBuilder(columns)
    rng = np.random.default_rng(812)
    for row_index in range(25):
        count = int(rng.integers(0, columns + 1))
        indices = rng.choice(columns, size=count, replace=False)
        values = rng.choice([0.0, -3.25, -1.0, 2.5, 8.0], size=count)
        row = dict(zip(indices, values))
        (cb.le, cb.ge, cb.eq)[row_index % 3](row, row_index - 10.5)
    cb.eq({}, 0)
    cb.ge({0: 0}, 1)

    original = cb.build()
    original_bytes = _compiled_bytes(original)
    assert original_bytes == _compiled_bytes(_coo_reference(cb))
    assert cb.build() is original

    cb.le({columns - 1: -2.0, 0: 4.0}, 7.5)
    appended = cb.build()
    appended_bytes = _compiled_bytes(appended)
    assert appended_bytes == _compiled_bytes(_coo_reference(cb))
    assert _compiled_bytes(original) == original_bytes
    assert cb.build() is appended

    cb.n += 2
    expanded = cb.build()
    expanded_bytes = _compiled_bytes(expanded)
    assert expanded_bytes == _compiled_bytes(_coo_reference(cb))
    assert _compiled_bytes(appended) == appended_bytes

    cb.eq({cb.n - 1: 1.5, 0: -1.0}, 3)
    assert _compiled_bytes(cb.build()) == _compiled_bytes(_coo_reference(cb))
    assert _compiled_bytes(expanded) == expanded_bytes
    assert _compiled_bytes(original) == original_bytes


def test_new_cut_changes_solution_without_mutating_previous_matrix():
    cb = ConstraintBuilder(3)
    cb.ge({0: 1, 1: 1}, 1)
    cb.le({0: 1, 1: 1, 2: 0}, 1)
    cb.eq({2: 1}, 0)
    original = cb.build()

    def solve(constraints):
        result = milp([1, 2, 0], bounds=Bounds(0, 1), integrality=1, constraints=constraints)
        assert result.status == 0
        return result.x

    np.testing.assert_allclose(solve(original), [1, 0, 0])
    np.testing.assert_allclose(solve(cb.build()), [1, 0, 0])
    cb.le({0: 1}, 0)
    np.testing.assert_allclose(solve(cb.build()), [0, 1, 0])
    np.testing.assert_allclose(solve(original), [1, 0, 0])


def test_empty_coefficient_row_can_still_make_model_infeasible():
    cb = ConstraintBuilder(2)
    cb.ge({0: 0}, 1)
    result = milp([1, 0], bounds=Bounds(0, 1), integrality=1, constraints=cb.build())
    assert result.status == 2


def test_copied_constraints_keep_the_new_auxiliary_column():
    base = ConstraintBuilder(2)
    base.eq({0: 1, 1: 1}, 1)
    base.build()
    extended = ConstraintBuilder(3)
    extended.rows.extend(base.rows)
    extended.ge({2: 1, 0: -2, 1: -1}, 0)
    result = milp([0, 0, 1], bounds=Bounds(0, [1, 1, 100]), integrality=[1, 1, 0], constraints=extended.build())
    assert result.status == 0
    np.testing.assert_allclose(result.x, [0, 1, 1])
