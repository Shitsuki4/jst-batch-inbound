"""Read-only, account-scoped PO discovery and confirmed no-PO receipt linking.

The product catalog scope is deliberately NOT the purchase-query scope. Every
selected purchase is read again in its exact ERP scope before a local commit.
No function in this module creates or confirms an ERP receipt.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
import unicodedata
from decimal import Decimal

from inbound import json_text
from jst import BizError, normalize_po_item, normalize_po_row
from store import now_str

_KEY = secrets.token_bytes(32)
_ALL = '2147483647'


def account(client):
    if not client.uid or not client.coid:
        raise ValueError('采购单查询需要登录，请重新登录聚水潭')
    return [str(client.uid), str(client.coid)]


def concrete(value):
    return str(value or '').strip() not in ('', '0', _ALL)


def signature(value):
    return hmac.new(_KEY, json.dumps(value, ensure_ascii=False, sort_keys=True,
                    separators=(',', ':'), allow_nan=False).encode(), hashlib.sha256).hexdigest()


def seal(value):
    data = base64.urlsafe_b64encode(json_text(value).encode()).decode()
    return data + '.' + signature(data)


def unseal(token):
    try:
        data, tag = str(token).split('.')
        if len(data) > 8000 or not hmac.compare_digest(tag, signature(data)):
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(data))
        if time.time() > value['expires']: raise ValueError()
        return value
    except Exception:
        raise ValueError('采购单选择已失效或被修改，请重新查询') from None


def discover(client, no, source):
    """Queries only with the current ERP login; ignores stored product/global picks.

    Generic owner queries are tried in each available warehouse. Some ERP tenants
    require concrete owners, so an explicitly bounded fallback is also provided.
    Partial lists are labelled; an apparent unique result is never auto-selected.
    """
    _, company = account(client)
    owners, auths, warnings = [company], [company], []
    for method, dest, label in (('partners', owners, '货主'), ('wms_list', auths, '分仓')):
        try:
            values = getattr(client, method)()
            if not isinstance(values, list) or any(not isinstance(r, dict) for r in values):
                raise ValueError('清单响应格式异常')
            if not values: warnings.append(label+'清单为空，可能未完整读取；可刷新登录 / 权限后重试')
            for row in values:
                ident = str(row.get('id') or '').strip()
                if concrete(ident) and ident not in dest: dest.append(ident)
        except (BizError, OSError, ValueError) as exc:
            if any(t in str(exc) for t in ('登录', '验证码', '风控')):
                raise ValueError('采购单查询未完成：'+str(exc)) from exc
            warnings.append(label+'清单未完整读取，请刷新登录/权限后重试')
    # The existing source is a hint, not a restriction and not an authorization.
    # ERP validates every request; selection and commit re-read the actual target.
    hints = [tuple(source)] if all(concrete(x) for x in source) else []
    scopes = list(dict.fromkeys(hints + [(_ALL, a) for a in auths]))
    fallback = [(o, a) for a in auths for o in owners if (o, a) not in scopes]
    found, errors, queried = {}, [], []

    def query(scope):
        owner, auth = scope
        queried.append(scope)
        for kind in ('po_id', 'outer_po_id'):
            try:
                rows, _ = client.search_po(no, owner, auth, kind)
                if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                    raise ValueError('采购列表响应格式异常')
            except (BizError, OSError, ValueError) as exc:
                if any(t in str(exc) for t in ('登录', '验证码', '风控')):
                    raise ValueError('采购单查询未完成：'+str(exc)) from exc
                errors.append(str(exc) or type(exc).__name__)
                continue
            if len(rows) >= 25:
                warnings.append('查询结果较多，以下可能不是全部；请优先使用具体采购单号')
            for raw in rows:
                head = normalize_po_row(raw)
                if not head['po_id'] or no not in (head['po_id'], head['outer_po_id']): continue
                target_owner = head['owner_co_id'] or (owner if concrete(owner) else '')
                target_auth = head['wms_co_id'] or auth
                if not all(concrete(x) for x in (target_owner, target_auth)):
                    warnings.append('部分采购单未返回具体货主 / 分仓，不能安全关联')
                    continue
                key = (head['po_id'], target_owner, target_auth)
                found[key] = dict(head=head, owner=target_owner, auth=target_auth)

    for scope in scopes[:16]: query(scope)
    if len(scopes) > 16: warnings.append('分仓范围较多，本次仅查询前16个范围；可使用具体采购单号缩小查询')
    if not found:
        for scope in fallback[:16]: query(scope)
        if len(fallback) > 16: warnings.append('货主 / 分仓组合较多，本次未查询全部范围，请核对具体采购单号及权限')
    if errors: warnings.append('部分范围查询失败：'+'；'.join(dict.fromkeys(errors))[:180])
    if not found:
        if errors or warnings:
            raise ValueError('采购单查询未完成或未找到精确单据。'+'；'.join(dict.fromkeys(warnings)))
        raise ValueError('当前账号可查询范围内未找到精确采购单。支持采购单号或外部单号；请确认单据已保存及账号有采购查询权限。商品库范围不会限制本次查询。')
    return list(found.values()), list(dict.fromkeys(warnings))


def _norm(value):
    return ''.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def comparison(lines, raw, cross_scope):
    received, plans = {}, {}
    for line in lines:
        sku = str(line['sku_id'])
        if sku not in received: received[sku] = dict(qty=Decimal(0), lines=[])
        received[sku]['qty'] += Decimal(str(line['qty']))
        received[sku]['lines'].append(line)
    for row in raw:
        item = normalize_po_item(row)
        plans.setdefault(item['sku_id'], []).append(item)
    rows, blockers = [], []
    for sku in sorted(set(received) | set(plans)):
        source = received.get(sku, dict(qty=Decimal(0), lines=[]))
        target = plans.get(sku, [])
        issues = []
        if len(target) != 1:
            issues.append('目标采购单无此SKU' if not target else '目标采购单有重复SKU行')
        dest = target[0] if len(target) == 1 else {}
        if cross_scope and source['lines'] and dest:
            for field, title in (('name', '名称'), ('spec', '规格'), ('sku_code', '条码')):
                if any(_norm(r.get(field)) and _norm(dest.get(field)) and
                       _norm(r.get(field)) != _norm(dest.get(field)) for r in source['lines']):
                    issues.append(title+'不一致')
            if not _norm(dest.get('name')): issues.append('目标商品缺少名称，无法跨范围核对')
            if any(float(r['qty']) > 0 and not _norm(r.get('name')) for r in source['lines']):
                issues.append('原实收商品缺少名称，无法跨范围核对')
        if cross_scope and source['qty'] > 0 and issues:
            blockers.append(sku+'：'+'、'.join(issues))
        rows.append(dict(sku_id=sku, source=source['lines'][0] if source['lines'] else {}, target=dest,
                         received=float(source['qty']), planned=sum(p['qty'] for p in target), issues=issues))
    return rows, blockers


def link_action(service, body):
    from unplanned import clean
    sid = int(body.get('sid') or 0)
    action = body['action']
    service.guard(sid, body.get('revision'), reconciliation=True)
    who = account(service.client)
    old = service.session(sid)
    catalog = service.catalog_scope(sid)
    if action == 'match_search':
        no = clean(body.get('po_id'), 200)
        if not no: raise ValueError('请输入采购单号或外部单号')
        service.store.db.execute('UPDATE unplanned_meta SET checked_revision=-1 WHERE sid=?', (sid,))
        service.store.db.commit()
        candidates, warnings = discover(service.client, no, (old['owner_co_id'], old['auth_co_id']))
        choices = []
        for candidate in candidates:
            selected = dict(account=who, sid=sid, revision=body['revision'], query=no,
                            po_id=candidate['head']['po_id'], owner=candidate['owner'], auth=candidate['auth'],
                            expires=time.time()+600)
            choices.append(dict(candidate, selection=seal(selected)))
        return dict(link_candidates=choices, warnings=warnings, catalog_scope=catalog)
    selected = unseal(body.get('selection'))
    if (selected['account'] != who or selected['sid'] != sid or selected['revision'] != body['revision']):
        raise ValueError('账号或收货记录已变化，请重新查询采购单')
    po, owner, auth = selected['po_id'], selected['owner'], selected['auth']
    changed = (str(old['po_id']), old['owner_co_id'], old['auth_co_id']) != (po, owner, auth)
    if changed and service.store.inbound_jobs(sid):
        raise ValueError('该批次存在入库历史（含失败或待核实），不可更换采购关联，请核对原单')
    found, _ = service.client.search_po(po, owner, auth, 'po_id')
    exact = [normalize_po_row(r) for r in found if str(r.get('po_id') or '').strip() == po]
    if len(exact) != 1: raise ValueError('目标采购单已变化或无法唯一读取，请重新查询')
    head = exact[0]
    if ((head['owner_co_id'] and head['owner_co_id'] != owner) or
            (head['wms_co_id'] and head['wms_co_id'] != auth)):
        raise ValueError('目标采购单返回范围不一致，未关联')
    raw = service.client.fetch_detail(po, owner, auth).get('datas') or []
    service.purchase_items(raw)  # validate before any local association changes
    db = service.store.db
    db.execute('BEGIN IMMEDIATE')
    try:
        service.guard(sid, body.get('revision'), reconciliation=True)
        current = service.session(sid)
        if current != old or account(service.client) != who:
            raise ValueError('账号或收货批次已变化，请重新预览')
        if changed and service.store.inbound_jobs(sid):
            raise ValueError('该批次存在入库历史，不可更换采购关联，请核对原单')
        lines = [dict(r) for r in db.execute('SELECT * FROM line WHERE session_id=? ORDER BY id', (sid,))]
        cross = (catalog['owner'], catalog['auth']) != (owner, auth)
        confirm_scope = cross or bool(old['po_id'] and (old['owner_co_id'], old['auth_co_id']) != (owner, auth))
        rows, blockers = comparison(lines, raw, cross)
        snapshot = dict(account=who, selected=selected, original=old, catalog=catalog, lines=lines, head=head, raw=raw)
        fingerprint = signature(snapshot)
        preview = dict(selection=body['selection'], fingerprint=fingerprint, head=head, owner=owner, auth=auth,
                       original=dict(po_id=old['po_id'], owner=old['owner_co_id'], auth=old['auth_co_id']),
                       catalog_scope=catalog, cross_scope=confirm_scope, rows=rows, blockers=blockers,
                       received_total=sum(r['received'] for r in rows))
        if action == 'match_preview':
            db.rollback()
            return dict(link_preview=preview)
        if action != 'match_commit': raise ValueError('不支持的关联操作')
        if body.get('confirmed') is not True or not hmac.compare_digest(fingerprint, str(body.get('fingerprint') or '')):
            raise ValueError('确认失效或采购资料 / 实收已变化，请重新预览后关联')
        if confirm_scope and body.get('confirm_scope') is not True:
            raise ValueError('请明确勾选确认不同货主 / 分仓的采购关联')
        if blockers:
            raise ValueError('跨范围商品身份尚未核对通过，不能关联：'+'；'.join(blockers)[:400])
        # Preserve the original catalog identity even for migrated legacy batches.
        db.execute('UPDATE unplanned_meta SET catalog_owner=?,catalog_auth=? WHERE sid=?', (catalog['owner'],catalog['auth'],sid))
        service.write_purchase(sid, po, owner, auth, raw, head['outer_po_id'], head['supplier'])
        # A confirmation is a mutation even when the purchase data is unchanged.
        # Consume this revision so a saved confirmation ticket cannot be replayed.
        db.execute('UPDATE unplanned_meta SET revision=revision+1,checked_revision=revision+1 WHERE sid=?', (sid,))
        db.execute('INSERT INTO unplanned_link_audit(sid,at,detail) VALUES(?,?,?)',
                   (sid, now_str(), json_text(dict(account=who, original=preview['original'], catalog=catalog,
                                                  target=dict(po_id=po,owner=owner,auth=auth), rows=rows))))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return service.state(sid)
