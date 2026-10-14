# -*- coding: utf-8 -*-
"""Shared exact aliases. ERP _sku_codes / sku_auxiliary_code are comma-separated strings."""
import json
import re


def auxiliary_codes(value):
    if value is None: return []
    if isinstance(value, (list, tuple)):
        return list(dict.fromkeys(c for v in value for c in auxiliary_codes(v)))
    if isinstance(value, dict):
        return auxiliary_codes(value.get('sku_code') or value.get('code') or '')
    value = str(value).strip()
    if value.startswith('['):
        try: return auxiliary_codes(json.loads(value))
        except (ValueError, TypeError): pass
    return list(dict.fromkeys(c for c in re.split(r'[,，;；\s]+', value) if c))


def item_auxiliary(item):
    return list(dict.fromkeys(c for k in ('aux_codes', '_sku_codes', 'sku_auxiliary_code')
                             for c in auxiliary_codes(item.get(k))))


def product_enabled(value):
    """Only explicit ERP enabled flags count; missing/unrecognized is unknown."""
    if value is None: return None
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes', 'enabled', '启用', '已启用'): return True
    if text in ('-1', '0', 'false', 'no', 'disabled', '禁用', '已禁用', '停用', '已停用'): return False
    return None


def enabled_scan_candidates(rows, ignore_disabled):
    if not ignore_disabled: return rows
    result = [r for r in rows if product_enabled(r.get('enabled')) is not False]
    if rows and not result:
        raise ValueError('此编码命中的商品全部已禁用，未登记；请核对ERP商品资料，或关闭「扫码忽略禁用商品」后人工选择')
    return result
