"""Strict acceptance gate for same-algorithm fast/exact paired results.

Accuracy corrections are not a speed approximation and must not be fed into
this gate as the exact reference. Missing components are not treated as zero.
"""
import math

FIELDS = ('total_score', 'output_score', 'support_score', 'specialty_score')


def compare_scores(exact, fast, *, limit=3.0):
    if not math.isfinite(limit) or limit < 0:
        raise ValueError('invalid tolerance')
    errors, differences = [], []
    def index(rows, label):
        result = {}
        for row in rows:
            key = (row['case'], row['source_id'])
            if key in result:
                errors.append(f'{label}:duplicate:{key}')
            result[key] = row
        return result
    a, b = index(exact, 'exact'), index(fast, 'fast')
    if not a or set(a) != set(b):
        errors.append('empty or unequal participant sets')
    for key in sorted(set(a) & set(b)):
        if a[key].get('role') != b[key].get('role'):
            errors.append(f'role changed:{key}')
        for field in FIELDS:
            if field not in a[key] or field not in b[key]:
                errors.append(f'missing field:{key}:{field}')
                continue
            x, y = a[key][field], b[key][field]
            if x is None and y is None:
                continue
            if x is None or y is None or not all(math.isfinite(v) for v in (x, y)):
                errors.append(f'nonfinite or changed applicability:{key}:{field}')
                continue
            difference = y - x
            differences.append(dict(case=key[0], source_id=key[1], metric=field,
                                    exact=x, fast=y, delta=difference))
            if abs(difference) > limit:
                errors.append(f'over limit:{key}:{field}:{difference}')
    return dict(accepted=not errors, limit=limit, errors=errors,
                pairs=len(set(a) & set(b)), differences=differences,
                max_absolute_delta=max((abs(r['delta']) for r in differences), default=None))
