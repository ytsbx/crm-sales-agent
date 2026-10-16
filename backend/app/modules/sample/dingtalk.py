"""打样两条钉钉路径：独立发单 / 关联原询价；外部实例统一复用 OA 防重状态机。"""
import json
import httpx
from datetime import date
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.dingtalk import service as oa
from app.modules.dingtalk.client import get_client, DingTalkError
from app.modules.dingtalk.model import OaInstance
from app.modules.sample import service
from app.modules.sample.model import SampleItem

router = APIRouter(tags=['Sample'])
META_KEY = 'dingtalk_oa'
CONFIG_KEY = 'dingtalk_sample_oa'
SYSTEM_LABELS = {'打样申请编号','申请人员','归属部门','样品明细明细表'}
INPUT_LABELS = {'打样类型','打样产品大类','样品制作单位','要求完成日期','本次打样积分','打样需求说明'}


def metadata(sample):
    return (sample.source_context or {}).get(META_KEY) or {}


def snapshot_edit_detail(snapshot:dict,incoming:dict)->str:
    """这次重试**有没有真的改写审批快照**？改写返回差异描述，没改写返回空串。

    ⚠️ **只比"两边都有"的字段**（主人 2026-10-16：「我没改写啊」）。
    快照里存的是**用户填的那批**；而 `申请人员` / `归属部门` / `样品明细明细表`
    这三项**每次提交时由后端现算**（钉钉身份、部门、现生成的 Excel 附件），
    **根本不在快照里**。前端"重试原申请"会把它们一起带回来 ——
    若按"整个 fields 直接比对"，用户什么都没改却永远被告知"你改写了快照"，
    而且**没有出路**（那三项永远不可能与快照相等）。那是个死循环。

    抽成独立函数是为了**能被单测直接钉住** —— 这段判据曾经写错过两次。
    """
    changed={k:(v,snapshot[k]) for k,v in incoming.items() if k in snapshot and v!=snapshot[k]}
    return '；'.join(f'{k}：{old!r} → {new!r}' for k,(new,old) in list(changed.items())[:3])


def _is_image(filename:str,mime:str)->bool:
    """这个附件是不是**图片**（决定它能不能放进申请表的「产品图片」那格）。

    两个判据都用：`mime_type` 是上传时写的（可能为空/写错），
    扩展名是用户文件名的（也可能骗人）—— 任一命中即算，宁可多试一张
    （生成器里读不出来的图会**跳过**，不影响整份申请表）。
    PDF / Excel / Word 一律不算 —— 它们放不进格子，仍走钉钉附件那条路。
    """
    if mime.startswith('image/'):return True
    return filename.lower().rsplit('.',1)[-1] in ('png','jpg','jpeg','gif','bmp','webp','tif','tiff')


def ensure_local_review(sample):
    if metadata(sample):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'此单使用钉钉流程，请在钉钉处理审批，CRM只同步查看',409)


def ensure_editable(sample):
    if metadata(sample).get('submitted'):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'这张打样申请已经关联或提交钉钉，不能在CRM修改、删除或重批；请先在钉钉处理原单',409)


async def template(session, *, for_display=False):
    from app.modules.settings.service import get_setting
    cfg = await get_setting(session, CONFIG_KEY) or {}
    code = cfg.get('process_code')
    if not code or settings.dingtalk_push_off:
        return {'configured':bool(code),'enabled':not settings.dingtalk_push_off,'fields':[]},None
    try:
        client = get_client()
        schema = await (client.get_display_process_schema(code) if for_display else client.get_process_schema(code))
    except (DingTalkError,httpx.HTTPError) as error:
        raise AppError(ErrorCode.EXTERNAL_ERROR,'无法读取钉钉打样模板，可稍后重试，未发出审批',502) from error
    content = schema.get('schemaContent') or {}
    if isinstance(content,str):content=json.loads(content)
    fields=[]
    for item in content.get('items',[]):
        props=item.get('props') or {};label=props.get('label');kind=item.get('componentName')
        if label not in INPUT_LABELS | SYSTEM_LABELS:continue
        options=[]
        for option in props.get('options') or []:
            if isinstance(option,str) and option.startswith('{'):option=json.loads(option)
            value=option.get('value') if isinstance(option,dict) else option
            if value is not None:options.append(str(value))
        fields.append({'id':props['id'],'label':label,'type':kind,'required':bool(props.get('required')),
                       'options':options,'format':props.get('format'),'system':label in SYSTEM_LABELS})
    if not INPUT_LABELS.issubset({f['label'] for f in fields}):
        raise AppError(ErrorCode.PARAM_ERROR,'钉钉打样模板已变化，请管理员核对字段',422)
    return {'configured':True,'enabled':True,'fields':fields},code


async def inquiry_candidates(session,user,sample):
    from app.modules.inquiry.service import get_visible_or_404
    if not user.has('quote:view'):return []
    ids=set((await session.scalars(select(SampleItem.inquiry_id).where(SampleItem.sample_request_id==sample.id,
                                                                               SampleItem.inquiry_id.is_not(None)))).all())
    context=sample.source_context or {}
    if context.get('type')=='inquiry' and context.get('id'):ids.add(context['id'])
    result=[]
    for inquiry_id in sorted(ids):
        try:inquiry=await get_visible_or_404(session,user,inquiry_id)
        except AppError:continue
        if inquiry.customer_id not in (None,sample.customer_id):continue
        rows=(await session.scalars(select(OaInstance).where(OaInstance.inquiry_id==inquiry_id,
                 OaInstance.inquiry_version==inquiry.version,OaInstance.oa_type=='inquiry',
                 OaInstance.instance_id.is_not(None),OaInstance.status.in_(['pending','approved','rejected','cancelled'])).order_by(OaInstance.id.desc()))).all()
        for row in rows:result.append({'id':row.id,'label':f'{inquiry.title} · 第{row.submit_round}轮 · {oa.serialize(row)["status_label"]}'})
    return result


def collect_template_values(fields_meta, supplied):
    """按真实模板读这次要提交的值，并做**逐字段**校验。

    返回 `(component_id -> 值, 标签 -> 值)`；后者的用途是生成申请表/预计工时，
    所以标签键必须来自模板真实字段（`template()` 已保证这些标签都在）。

    ⚠️ 选填字段留空 = "这次不填"，**不是格式错**（2026-10-16 修）：
    原先对空串直接 `date.fromisoformat('')` / `Decimal('')`，两者都抛，于是
    用户什么都没填却被告知"要求完成日期格式无效"/"打样积分必须是0到99990之间
    的有限数字" —— 一句与事实不符的报错。
    """
    values = {}
    by_label = {}
    for field in fields_meta:
        if field['system']:
            continue
        value = str(supplied.get(field['id'], '') or '').strip()
        if field['required'] and not value:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, f'请填写{field["label"]}', 422)
        if value:
            if field['options'] and value not in field['options']:
                raise AppError(ErrorCode.PARAM_ERROR, f'{field["label"]}选项无效', 422)
            if field['type'] == 'DDDateField':
                try:
                    date.fromisoformat(value)
                except ValueError as error:
                    raise AppError(ErrorCode.PARAM_ERROR, '要求完成日期格式无效', 422) from error
            if field['type'] == 'NumberField':
                try:
                    number = Decimal(value)
                    if not number.is_finite() or number < 0 or number > 99990:
                        raise ValueError()
                except (InvalidOperation, ValueError) as error:
                    raise AppError(ErrorCode.PARAM_ERROR, '打样积分必须是0到99990之间的有限数字', 422) from error
        values[field['id']] = value
        by_label[field['label']] = value
    return values, by_label


class Draft(BaseModel):
    mode: Literal['standalone','inquiry']
    source_oa_id: int | None = Field(default=None,gt=0)
    fields: dict[str,str] = Field(default_factory=dict,max_length=30)
    resubmit: bool = False


@router.get('/samples/{sample_id}/dingtalk')
async def detail(sample_id:int,user:CurrentUser=Depends(require_permission('sample:manage')),session:AsyncSession=Depends(get_db)):
    sample=await service.get_visible_or_404(session,user,sample_id)
    item_count=len((await session.scalars(select(SampleItem.id).where(SampleItem.sample_request_id==sample.id))).all())
    try:options,_=await template(session,for_display=True)
    except AppError as error:options={'configured':False,'enabled':False,'fields':[],'template_error':error.message}
    meta=metadata(sample)
    records=[]
    if meta.get('source_oa_id') and meta.get('submitted'):
        # 关联记录必须仍通过原询价的数据范围，不能用 sample:view 绕过 quote:view。
        candidates=await inquiry_candidates(session,user,sample)
        if meta['source_oa_id'] in {r['id'] for r in candidates}:
            row=await session.get(OaInstance,meta['source_oa_id'])
            if row:records=[oa.serialize(row)]
    else:
        records=[oa.serialize(row) for row in (await session.scalars(select(OaInstance).where(
            OaInstance.inquiry_id==sample.id,OaInstance.inquiry_version==int(sample.version or 1),OaInstance.oa_type=='sample').order_by(OaInstance.id.desc()))).all()]
    # ⚠️ 回填的字段表要**跟 save() 里落库的那批一致**（2026-10-16 补全）：
    # 原来只回填那 7 项，于是"打样目的 / 紧急程度 / 样品情况 / 打样费用"
    # 在**重新打开**弹窗时是空的 —— 库里明明存着，界面上却看不见，
    # 用户会以为没存上、再填一遍（主人这一轮报的就是这类"看不见"）。
    _PREFILL = ('product_name','material_spec','product_size','printing','accessories',
                'packaging','craft_requirement','sample_purpose','urgency','sample_situation','sample_fee',
                'deliver_date','sample_maker')
    _prefill = {'_' + key: str(getattr(sample, key) or '') for key in _PREFILL}
    # 「要求完成日期」是**模板自带的控件**（id 由模板给），它的值落在
    # `sample.target_completion_date` 这一列上（`save()` 里落的）。
    # 不把列值按控件 id 填回去，重开弹窗那一格就是空的 —— 而 Excel 的
    # 「交样时间」正是取这一列（主人 2026-10-16 问「交样时间有没有」）。
    _due_field = next((f for f in (options.get('fields') or []) if f.get('label') == '要求完成日期'), None)
    if _due_field is not None and sample.target_completion_date:
        _prefill[_due_field['id']] = sample.target_completion_date.isoformat()
    return ok({**options,'item_count':item_count,'draft':{**meta,'fields':{**_prefill,**(meta.get('fields') or {})}},'candidates':await inquiry_candidates(session,user,sample),'records':records})


async def save(session,user,sample,payload,options=None):
    if metadata(sample).get('submitted') and payload.resubmit:
        previous=await oa.get_by_business_key(session,inquiry_id=sample.id,inquiry_version=int(sample.version or 1),oa_type='sample')
        if metadata(sample).get('mode')!='standalone' or payload.mode!='standalone' or previous is None or previous.status not in ('rejected','withdrawn'):
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'只有已驳回或已撤回的独立打样审批可以修改后重提',409)
    else:ensure_editable(sample)
    if sample.status not in ('pending','rejected') or sample.made_at:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'只有尚未进入制作的待审或驳回申请能配置钉钉流程',409)
    if payload.mode=='inquiry' and payload.source_oa_id is not None:
        if payload.source_oa_id not in {r['id'] for r in await inquiry_candidates(session,user,sample)}:
            raise AppError(ErrorCode.PARAM_ERROR,'请选择这张样品真实来源询价的钉钉审批，不能关联其他客户或其他来源',422)
    sample.source_context={**(sample.source_context or {}),META_KEY:payload.model_dump()}
    # 弹窗里填的「打样目的 / 紧急程度 / 样品情况」**落到正式列**（2026-10-16）。
    #
    # 为什么必须落列：申请表 Excel 的 C4/G4/C5 三格是生成器从 `sample_purpose` /
    # `urgency` / `sample_situation` 这三**列**读的。若只存进 metadata（草稿快照），
    # 这三格就永远是空的 —— 而钉钉模板里又没有这三项控件，用户**没有任何地方能填**
    # （主人 2026-10-16：「还有打样目的，紧急程度…为什么不让我填」）。
    #
    # ⚠️ 只在**还没提交**时写：提交后那张申请表就是审批快照，不能再改
    # （重试必须用原始资料，见 submit 里那条 409）。`save()` 的调用方在
    # 已提交时要么被 `ensure_editable` 拦住、要么显式跳过 save，所以这里再判一次。
    if not metadata(sample).get('submitted'):
        for _key, _column in (('sample_purpose','sample_purpose'),('urgency','urgency'),('sample_situation','sample_situation')):
            _value = payload.fields.get('_' + _key)
            if _value is not None:
                setattr(sample, _column, _value or None)
        # 「打样费用」同样落列（2026-10-16 主人：「打样费用在哪没看见」）——
        # 它在申请表 C3 那格，以前只能去详情页的「生产打样资料」里填，
        # 而钉钉弹窗是建完单立刻弹出来的，用户在那儿找不到它。
        # 空串 = 明确清空（与"没传"区分：没传就不动）。
        # 「要求完成日期」是**钉钉模板自带的控件**（不是 `_` 前缀那批），
        # 所以以前只有提交时才带过去、**保存草稿不落库** —— 重开弹窗那一格是空的，
        # 而 Excel 的「交样时间」正是从这一列取的（主人 2026-10-16 问「交样时间有没有」）。
        # 这里按**控件标签**找它（id 是模板给的，不写死）。
        # ⚠️ **不要在这里 await template(session)**（2026-10-16）：
        # `save()` 是**纯函数式**的（`tests/test_sample_dingtalk.py` 用假 session 调它，
        # 本意就是"保存草稿不碰模板/不连库"）。我第一版在那里取了模板，四个测试立刻红。
        # 所以模板由**调用方**传进来 —— 两个路由本来就已经取过它了。
        _fields = (options or {}).get('fields') or []
        _due_field = next((f for f in _fields if f.get('label') == '要求完成日期'), None)
        if _due_field is not None:
            _due = payload.fields.get(_due_field['id'])
            if _due is not None:
                _due_text = str(_due).strip()
                if not _due_text:
                    sample.target_completion_date = None
                else:
                    try:
                        sample.target_completion_date = date.fromisoformat(_due_text)
                    except ValueError:
                        raise AppError(ErrorCode.PARAM_ERROR, '要求完成日期格式不对（应为 YYYY-MM-DD）', 422)
        # 「交样时间」（申请表 H2）与「打样人」（申请表 H3）同样落列 ——
        # 它们与钉钉的「要求完成日期」是**两件事**（主人 2026-10-16 指出）。
        _deliver = payload.fields.get('_deliver_date')
        if _deliver is not None:
            _dt = str(_deliver).strip()
            if not _dt:
                sample.deliver_date = None
            else:
                try:
                    sample.deliver_date = date.fromisoformat(_dt)
                except ValueError:
                    raise AppError(ErrorCode.PARAM_ERROR, '交样时间格式不对（应为 YYYY-MM-DD）', 422)
        _maker = payload.fields.get('_sample_maker')
        if _maker is not None:
            sample.sample_maker = str(_maker).strip() or None
        _fee = payload.fields.get('_sample_fee')
        if _fee is not None:
            _text = str(_fee).strip()
            if not _text:
                sample.sample_fee = None
            else:
                try:
                    from decimal import Decimal as _Decimal
                    sample.sample_fee = _Decimal(_text)
                except Exception:
                    raise AppError(ErrorCode.PARAM_ERROR, '打样费用要填一个不小于 0 的数字', 422)
    await session.flush()


@router.put('/samples/{sample_id}/dingtalk-draft')
async def save_draft(sample_id:int,payload:Draft,request:Request,user:CurrentUser=Depends(require_permission('sample:manage')),session:AsyncSession=Depends(get_db)):
    sample=await service.get_visible_or_404(session,user,sample_id,for_update=True,allow_external_flow=True)
    # 取模板是为了知道**「要求完成日期」那个控件的 id**（按标签找，不写死 id）——
    # `save()` 要靠它把那个日期落进 `target_completion_date` 列。取不到就给空表，
    # 保存草稿不该因为钉钉模板读不出来而失败。
    try:
        options,_ = await template(session)
    except AppError:
        options = {'fields': []}
    await save(session,user,sample,payload,options)
    await write_audit(session,operator_id=user.id,action='update',business_type='sample',business_id=sample.id,
                      after={'dingtalk_draft':metadata(sample)},ip=client_ip(request))
    await session.commit()
    return ok(None,'草稿已保存，未发起钉钉审批')


@router.post('/samples/{sample_id}/dingtalk-submit')
async def submit(sample_id:int,payload:Draft,request:Request,user:CurrentUser=Depends(require_permission('sample:manage')),session:AsyncSession=Depends(get_db)):
    sample=await service.get_visible_or_404(session,user,sample_id,for_update=True,allow_external_flow=True)
    meta=metadata(sample)
    previous=await oa.get_by_business_key(session,inquiry_id=sample.id,inquiry_version=int(sample.version or 1),oa_type='sample') if payload.mode=='standalone' else None
    open_new_round=bool(payload.resubmit and previous is not None and previous.status in ('rejected','withdrawn'))
    retry=False
    if meta.get('submitted'):
        if payload.mode!=meta.get('mode') or payload.source_oa_id!=meta.get('source_oa_id'):
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'已经提交或关联，不能切换流程重复发单',409)
        retry=previous is not None and previous.status in ('failed','not_sent','skipped')
        if not retry and not open_new_round:
            return ok(oa.serialize(previous) if previous else None,'已提交或关联，请查看审批过程，不要重复发起')
        # ⚠️ **只在"原样重发"时锁快照，不锁"驳回后重提"**（2026-10-16 修）：
        # `retry` 是"上次没发出去（failed/not_sent/skipped）→ 原样重发"，这时必须一字不改，
        # 否则会拿新内容去覆盖一条"已经发生过"的审批快照。
        # 但 `open_new_round`（驳回/撤回后重新发起）**本来就该允许改** —— 主人这一轮就是
        # 被这条误拦的：他说"我没改写啊"，而真正变的是**我们自己算出来的**「本次打样积分」
        # （由工时 × 10 自动同步）。把"驳回后重提"也锁上，等于把这条规则和上面
        # `open_new_round` 的判定自相矛盾。
        # ⚠️ **只比"两边都有"的字段**（2026-10-16 第二次修，这次是真因）：
        # 快照里存的是**用户填的那批**；而 `申请人员` / `归属部门` / `样品明细明细表`
        # 这三项**每次提交时由后端现算**（钉钉身份、部门、现生成的 Excel 附件），
        # **根本不在快照里**。前端"重试原申请"会把它们一起带回来，
        # 于是 `payload.fields != meta["fields"]` 永远成立 ——
        # 用户看到的是一句「重试必须使用已提交的原始资料，不能改写审批快照」，
        # 而他**什么都没改**（主人 2026-10-16：「我没改写啊」）。那是个死循环。
        #
        # 所以取交集再比：后端自己算的那几项不参与比较。
        if retry and not open_new_round:
            _detail = snapshot_edit_detail(meta.get("fields") or {}, payload.fields)
            if _detail:
                raise AppError(ErrorCode.STATUS_NOT_ALLOWED,
                               f"重试必须使用已提交的原始资料，不能改写审批快照（{_detail}）", 409)
    # 模板**只在独立发起时取**：`save()` 要靠它知道「要求完成日期」那个控件的 id。
    # 「沿用原询价」那条路不发新的钉钉单，不需要模板 —— 而且那条路在集成测试里
    # 用的是假 session（读设置要连库），不去取也避免把测试弄红。
    options=None
    if payload.mode=='standalone':
        options,code=await template(session)
    if not retry:await save(session,user,sample,payload,options)
    if payload.mode=='inquiry':
        if not payload.source_oa_id:raise AppError(ErrorCode.REQUIRED_FIELD_MISSING,'请选择原询价审批单',422)
        sample.source_context={**sample.source_context,META_KEY:{**metadata(sample),'submitted':True}}
        await write_audit(session,operator_id=user.id,action='update',business_type='sample',business_id=sample.id,
                         after={'linked_inquiry_oa':payload.source_oa_id},ip=client_ip(request))
        await session.commit()
        return ok(None,'已关联原询价审批；打样步骤请在原钉钉单继续处理，本次没有新发审批')
    if not options['configured'] or not options['enabled']:
        raise AppError(ErrorCode.PARAM_ERROR,'独立打样模板未配置或钉钉推送未开启，请先保存草稿',422)
    fields={f['label']:f for f in options['fields']}
    values,by_label=collect_template_values(options['fields'],payload.fields)
    from app.modules.user.model import User
    account=await session.get(User,user.id)
    if account is None or account.status!='active' or not account.dingtalk_userid:
        raise AppError(ErrorCode.PARAM_ERROR,'请管理员先绑定当前账号的钉钉身份，再提交打样审批',422)
    client=get_client();departments=await client.get_user_dept_ids(account.dingtalk_userid)
    if not departments:raise AppError(ErrorCode.PARAM_ERROR,'钉钉未返回申请人的部门，请先核对',422)
    for label,value in [('申请人员',account.dingtalk_userid),('归属部门',departments[0])]:
        if label not in fields:raise AppError(ErrorCode.PARAM_ERROR,f'打样模板缺少{label}',422)
        values[fields[label]['id']]=value
    # 自动用现有公司底稿生成样品明细表，随后上传为真正的钉钉附件。
    from app.modules.sample.application_form import TEMPLATE_FILENAME, render_application_form
    from app.modules.dingtalk.attachments import upload_approval_attachment
    items=(await session.scalars(select(SampleItem).where(SampleItem.sample_request_id==sample.id))).all()
    attrs={column.key:getattr(sample,column.key) for column in sample.__table__.columns}
    for key in ('material_spec','product_size','printing','accessories','packaging','craft_requirement'):
        value=payload.fields.get('_'+key)
        if value is not None:attrs[key]=value
    # 「打样目的 / 紧急程度 / 样品情况」同样以**本次提交的值**为准（其次才是列里已有的）。
    # 这三项在钉钉模板里没有控件（13 个控件核对过），只能由 CRM 带过去 —— 申请表
    # 的 C4/G4/C5 三格就是它们。`save()` 已经把值写进列了，这里再兜一道是为了
    # 兼容"以前只存在草稿 metadata 里"的老单子。
    for key in ('sample_purpose','urgency','sample_situation'):
        value=payload.fields.get('_'+key)
        if value is not None:attrs[key]=value or None
    # 打样费用（申请表 C3）：同样以**本次提交的值**为准，列值兜底
    _deliver=payload.fields.get('_deliver_date')
    if _deliver is not None:
        from datetime import date as _date
        _t=str(_deliver).strip()
        attrs['deliver_date']=_date.fromisoformat(_t) if _t else None
    _maker=payload.fields.get('_sample_maker')
    if _maker is not None:attrs['sample_maker']=str(_maker).strip() or None
    _fee=payload.fields.get('_sample_fee')
    if _fee is not None:
        _t=str(_fee).strip()
        from decimal import Decimal as _D
        attrs['sample_fee']=_D(_t) if _t else None
    # 这几个值直接取自模板字段（`template()` 已保证这些标签都在），但**选填的日期/积分
    # 可以是空的**：空就别往申请表上写，而不是拿 '' 去 fromisoformat / Decimal ——
    # 那会让"选填留空"在提交的最后一步炸成一个 500。
    attrs.update(sample_type=by_label['打样类型'],product_category=by_label['打样产品大类'],maker_unit=by_label['样品制作单位'])
    if by_label['要求完成日期']:
        attrs['target_completion_date']=date.fromisoformat(by_label['要求完成日期'])
    if by_label['本次打样积分']:
        attrs['estimated_hours']=Decimal(by_label['本次打样积分'])/10
    from app.modules.sample.oa_content import dingtalk_requirement_note, submission_summary
    # Excel 保存完整单头和逐条明细；钉钉文本框只放真正的说明及附件提示，避免和
    # 原生字段、Excel 内容在审批详情中重复。
    summary=submission_summary(SimpleNamespace(**attrs),list(items),note=by_label['打样需求说明'],applicant_name=user.name)
    values[fields['打样需求说明']['id']]=dingtalk_requirement_note(
        by_label['打样需求说明'], attachment_name=TEMPLATE_FILENAME
    )
    attrs['oa_submission_summary']=summary
    if '样品明细明细表' not in fields:raise AppError(ErrorCode.PARAM_ERROR,'钉钉模板缺少样品明细表字段',422)
    from app.modules.file.model import FileRecord, BusinessFile
    from app.core.deps import ensure_permission
    from app.modules.file.storage import absolute_path
    linked=(await session.execute(select(FileRecord, BusinessFile.category).join(BusinessFile,BusinessFile.file_id==FileRecord.id).where(
        BusinessFile.business_type=='sample',BusinessFile.business_id==sample.id).order_by(BusinessFile.id))).all()
    if linked:ensure_permission(user,'file:view')
    prepared=[]
    _images=[]
    image_ids=set()
    for file,category in linked:
        is_image=_is_image(file.file_name,file.mime_type or '')
        if category=='sample_product_image' or (not category and is_image):
            if not is_image:
                raise AppError(ErrorCode.PARAM_ERROR,f'产品图片「{file.file_name}」不是有效图片，请重新上传',422)
            image_ids.add(file.id)
    seen=set()
    for file,category in linked:
        if file.id in seen:continue
        seen.add(file.id)
        path=absolute_path(file.object_key)
        if not path.is_file() or path.stat().st_size!=file.size:
            raise AppError(ErrorCode.PARAM_ERROR,f'文件「{file.file_name}」缺失或不完整，请重新上传',422)
        if file.id in image_ids:
            _images.append(path)
        else:
            prepared.append((file.file_name,path))
    if len(_images)>4:raise AppError(ErrorCode.PARAM_ERROR,'产品图片最多4张，请删除多余图片后重试',422)
    if len(prepared)>9:raise AppError(ErrorCode.PARAM_ERROR,'随单资料最多9份（另有1份自动生成的申请表），请先整理附件',422)
    try:
        # 申请表的「打样部门」对应业务上选择的样品制作单位；不要把内部提示语
        # “以钉钉归属部门为准”写进 Excel。钉钉归属部门仍保留在审批原生字段中。
        content=render_application_form(SimpleNamespace(**attrs),list(items),applicant_name=user.name,department_name=attrs.get('maker_unit') or '',product_images=_images,sample_maker=attrs.get('sample_maker') or '')
    except ValueError as error:
        raise AppError(ErrorCode.PARAM_ERROR,str(error),422) from error
    try:
        attachments=[await upload_approval_attachment(client,user_id=account.dingtalk_userid,filename=TEMPLATE_FILENAME,content=content)]
        for filename,path in prepared:
            attachments.append(await upload_approval_attachment(client,user_id=account.dingtalk_userid,filename=filename,content=path.read_bytes()))
    except (DingTalkError,ValueError,OSError,httpx.HTTPError) as error:
        # ⚠️ **把真实原因带上**（2026-10-16 主人：「为什么失败了」）：
        # 原来这里一律抛一句"请核对附件权限或稍后重试"，把 `DingTalkError` 里
        # 已经写好的**具体是哪一步失败**（附件空间查询 / 上传信息 / 签名 PUT / 入库）
        # 连同钉钉返回的 HTTP 状态码全吞掉了 —— 用户看到的是一句无从下手的提示，
        # 我也只能去猜。失败原因要能直接看到，才谈得上修。
        _detail = str(error) or error.__class__.__name__
        _status = getattr(error, 'http_status', None)
        if _status:
            _detail = f'{_detail}（钉钉 HTTP {_status}）'
        # 服务端日志也留一条原始记录：接口层面只回一句原因，排查时仍需要完整异常
        import logging as _logging
        _logging.getLogger('crm.sample').warning(
            '打样资料传入钉钉失败 sample=%s filename=%s: %s', sample.id,
            [f for f, _ in prepared], error, exc_info=True)
        raise AppError(ErrorCode.PARAM_ERROR,f'打样资料未能传入钉钉，尚未发起审批：{_detail}',422) from error
    values[fields['样品明细明细表']['id']]=json.dumps(attachments,ensure_ascii=False)
    # 先保存流程归属；OA 服务在调用外部前持久化唯一占位，断线不会自动重复建单。
    sample.source_context={**sample.source_context,META_KEY:{**metadata(sample),'submitted':True}}
    sample.status='pending'
    await session.flush()
    row=await oa.create_inquiry_instance(session,user=user,inquiry_id=sample.id,inquiry_version=int(sample.version or 1),
        customer_id=sample.customer_id,process_code=code,originator_user_id=account.dingtalk_userid,field_map=values,oa_type='sample',resubmit=open_new_round,
        field_definitions=options['fields'],dept_id=int(departments[0]))
    await write_audit(session,operator_id=user.id,action='update',business_type='sample',business_id=sample.id,
                      after={'dingtalk_oa_status':row.status,'instance_id':row.instance_id},ip=client_ip(request))
    await session.commit()
    return ok(oa.serialize(row),'请查看钉钉审批状态；审批在钉钉处理')
