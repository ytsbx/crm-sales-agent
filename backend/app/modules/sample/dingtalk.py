"""打样两条钉钉路径：独立发单 / 关联原询价；外部实例统一复用 OA 防重状态机。"""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, PositiveInt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.dingtalk import service as oa
from app.modules.dingtalk.model import OaInstance
from app.modules.oa import client as oa_client
from app.modules.oa import sample as oa_sample
from app.modules.sample import service
from app.modules.sample.model import SampleItem
from app.modules.sample.oa_content import validated_sample_details

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


def ensure_editable(sample):
    if metadata(sample).get('submitted'):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'这张打样申请已经关联或提交审批，不能在CRM修改、删除或重批；请先在对应审批系统处理原单',409)


def self_oa_available() -> bool:
    return oa_client.configured() and bool(
        settings.oa_sample_template_id.strip() or settings.oa_sample_template_code.strip()
    )


async def template(session, *, for_display=False, self_oa_only=False):
    # 打样审批只走自研 OA；钉钉审批路径已于 2026-11-25 删除。
    if self_oa_available():
        return {'configured': True, 'enabled': True, 'provider': 'self_oa', 'fields': oa_sample.oa_template_fields()}, None
    return {'configured': False, 'enabled': False, 'provider': 'self_oa',
            'fields': oa_sample.oa_template_fields(),
            'template_error': '尚未配置同序 OA 打样审批，可先保存草稿'}, None


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
                 OaInstance.instance_id.is_not(None),
                 OaInstance.provider == 'self_oa' if settings.dingtalk_push_off else True,
                 # 只列在审/已通过的：关联已驳回或已撤回的审批会让样品单再次卡死
                 OaInstance.status.in_(['pending','approved'])).order_by(OaInstance.id.desc()))).all()
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
    #: 同序「样品明细明细表」是上传附件，不再由 CRM 样品明细自动生成 Excel。
    sample_detail_file_ids: list[PositiveInt] = Field(default_factory=list, max_length=10)
    resubmit: bool = False


@router.get('/samples/{sample_id}/dingtalk')
async def detail(sample_id:int,user:CurrentUser=Depends(require_permission('sample:manage')),session:AsyncSession=Depends(get_db)):
    sample=await service.get_visible_or_404(session,user,sample_id)
    item_count=len((await session.scalars(select(SampleItem.id).where(SampleItem.sample_request_id==sample.id))).all())
    meta=metadata(sample)
    self_oa_only=bool(meta.get('requires_self_oa'))
    try:
        if self_oa_available() and settings.oa_sample_template_code.strip() == oa_sample.SOURCE_CODE:
            from app.modules.user.model import User
            options = await oa_sample.native_form(await session.get(User, user.id))
        else:
            options,_=await template(session,for_display=True,self_oa_only=self_oa_only)
    except AppError as error:
        # 同序读不到也不能把整个表单清空：按同序打样的字段继续展示，
        # 用户可以先把资料存草稿，等 OA 恢复后再提交。
        from app.modules.user.model import User
        from app.modules.oa.inquiry import _department_name
        account = await session.get(User, user.id)
        options={'configured':False,'enabled':False,'native_form': True,
                 'fields': oa_sample.oa_template_fields() if self_oa_available() else [],
                 'prefill': {'applicant_name': getattr(account, 'name', '') or '',
                             'department': await _department_name(session, account)
                             if getattr(account, 'department_id', None) else ''},
                 'template_error':error.message}
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
                'deliver_date','sample_maker','estimated_hours')
    _prefill = {}
    for key in _PREFILL:
        value = getattr(sample, key)
        _prefill['_' + key] = str(value) if value is not None else ''
    # 「要求完成日期」是**模板自带的控件**（id 由模板给），它的值落在
    # `sample.target_completion_date` 这一列上（`save()` 里落的）。
    # 不把列值按控件 id 填回去，重开弹窗那一格就是空的 —— 而 Excel 的
    # 「交样时间」正是取这一列（主人 2026-10-16 问「交样时间有没有」）。
    _due_field = next((f for f in (options.get('fields') or []) if f.get('label') == '要求完成日期'), None)
    if _due_field is not None and sample.target_completion_date:
        _prefill[_due_field['id']] = sample.target_completion_date.isoformat()
    draft_fields = {**_prefill, **(meta.get('fields') or {})}
    if options.get('provider') == 'self_oa':
        # 历史钉钉单存的是「外协厂」，同序模板的选项是「外协厂打样」。
        # 直接回填旧值会让下拉显示一个模板里不存在的选项，提交时被同序拒绝；
        # 这里按模板选项把旧值带成同序写法（不改库，用户保存样品时再落新值）。
        _maker = next((f for f in (options.get('fields') or []) if f.get('id') == 'maker_unit'), None)
        for field_id, value in {
            'sample_type': sample.sample_type, 'product_category': sample.product_category,
            'maker_unit': sample.maker_unit, 'sample_note': sample.sample_purpose or sample.remark,
            'score': sample.estimated_hours * 10 if sample.estimated_hours is not None else sample.craft_points,
        }.items():
            if value is not None:
                draft_fields.setdefault(field_id, str(value))
        if (draft_fields.get('maker_unit') == '外协厂' and not meta.get('submitted')
                and '外协厂打样' in ((_maker or {}).get('options') or [])):
            draft_fields['maker_unit'] = '外协厂打样'
    if options.get('provider') == 'self_oa' and not options.get('native_form'):
        from app.modules.user.model import User
        from app.modules.oa.inquiry import _department_name
        account = await session.get(User, user.id)
        options['prefill'] = {
            'applicant_name': getattr(account, 'name', '') or '',
            'department': await _department_name(session, account) if getattr(account, 'department_id', None) else '',
        }
    candidates=[] if self_oa_only else await inquiry_candidates(session,user,sample)
    from app.modules.file.model import BusinessFile, FileRecord
    detail_files = (await session.execute(
        select(FileRecord, BusinessFile.category).join(
            BusinessFile, BusinessFile.file_id == FileRecord.id
        ).where(BusinessFile.business_type == 'sample', BusinessFile.business_id == sample.id,
                BusinessFile.category == 'sample_details').order_by(FileRecord.id)
    )).all()
    return ok({**options,'item_count':item_count,'draft':{**meta,'fields':draft_fields},
               'detail_files':[{'id':row[0].id,'file_name':row[0].file_name} for row in detail_files],
               'candidates':candidates,'records':records})


async def _apply_field_columns(sample, payload, options=None):
    """把弹窗里用户填的字段落成正式列（《打样申请单》Excel 读这些列）。

    调用方决定什么时候可以落：首次提交/保存草稿时落；失败后改内容重试时也落
    （外部确定没建单，见 `submit`）；已经进入审批的单据不再落。
    """
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
    # 同序原生表单的「打样类型 / 产品大类 / 制作单位 / 积分」也要落列：
    # 下载/导出《打样申请单》Excel 读的是这些列，只存在草稿 metadata 里的话，
    # 保存草稿后立刻下载会看到打样类型、打样部门空着。
    if any(f.get('id') == 'sample_type' for f in _fields):
        for _name in ('sample_type', 'product_category', 'maker_unit'):
            if _name in payload.fields:
                setattr(sample, _name, payload.fields.get(_name) or None)
        _score = payload.fields.get('score')
        if _score not in (None, ''):
            try:
                sample.estimated_hours = Decimal(str(_score)) / 10
            except InvalidOperation as error:
                raise AppError(ErrorCode.PARAM_ERROR, '本次打样积分格式无效', 422) from error
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
    for field, value in validated_sample_details(payload.fields).items():
        setattr(sample, field, value)


async def remember_retry_fields(session, sample, payload, options=None):
    """失败/未发出后改字段重试：把这次内容更新为新的提交快照（B11-10）。

    只有外部**确定没建单**（failed / not_sent / skipped）才会走到这里：
    改了内容不会多出一张审批单，快照就该与这次真正提交的一致。
    provider / instance_id / submitted 等外部事实保持原样；
    结果未知（needs_review）不在此列——那边可能已经建了单，必须先核对。
    """
    current = metadata(sample)
    sample.source_context = {
        **(sample.source_context or {}),
        META_KEY: {
            **current,
            'fields': dict(payload.fields),
            'sample_detail_file_ids': list(payload.sample_detail_file_ids),
        },
    }
    await _apply_field_columns(sample, payload, options)
    await session.flush()


async def save(session,user,sample,payload,options=None):
    self_oa_only=bool(metadata(sample).get('requires_self_oa'))
    if self_oa_only and (payload.mode != 'standalone' or payload.source_oa_id is not None):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'此打样修订版须单独提交同序 OA 审批',409)
    if metadata(sample).get('submitted') and payload.resubmit:
        previous=await oa.get_by_business_key(session,inquiry_id=sample.id,inquiry_version=int(sample.version or 1),oa_type='sample')
        if metadata(sample).get('mode')!='standalone' or payload.mode!='standalone' or previous is None or previous.status not in ('rejected','withdrawn'):
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'只有已驳回或已撤回的独立打样审批可以修改后重提',409)
    else:ensure_editable(sample)
    if sample.status not in ('pending','rejected') or sample.made_at:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'只有尚未进入制作的待审或驳回申请能配置审批流程',409)
    if payload.mode=='inquiry' and payload.source_oa_id is not None:
        if payload.source_oa_id not in {r['id'] for r in await inquiry_candidates(session,user,sample)}:
            raise AppError(ErrorCode.PARAM_ERROR,'请选择这张样品真实来源询价的同序 OA 审批，不能关联其他客户或其他来源',422)
    draft=payload.model_dump()
    if self_oa_only:
        draft['requires_self_oa']=True
    sample.source_context={**(sample.source_context or {}),META_KEY:draft}
    # 弹窗里填的「打样目的 / 紧急程度 / 样品情况」**落到正式列**（2026-10-16）。
    #
    # 为什么必须落列：申请表 Excel 的 C4/G4/C5 三格是生成器从 `sample_purpose` /
    # `urgency` / `sample_situation` 这三**列**读的。若只存进 metadata（草稿快照），
    # 这三格就永远是空的 —— 而钉钉模板里又没有这三项控件，用户**没有任何地方能填**
    # （主人 2026-10-16：「还有打样目的，紧急程度…为什么不让我填」）。
    #
    # ⚠️ 只在**还没提交**时写：提交后那张申请表就是审批快照，不能再改。
    # 唯一的例外是「失败/未发出后改内容重试」—— 那条走 remember_retry_fields，
    # 不进这里；save() 的调用方在已提交时要么被 ensure_editable 拦住、要么跳过。
    if not metadata(sample).get('submitted'):
        await _apply_field_columns(sample, payload, options)
    await session.flush()


@router.put('/samples/{sample_id}/dingtalk-draft')
async def save_draft(sample_id:int,payload:Draft,request:Request,user:CurrentUser=Depends(require_permission('sample:manage')),session:AsyncSession=Depends(get_db)):
    sample=await service.get_visible_or_404(session,user,sample_id,for_update=True,allow_external_flow=True)
    # 取模板是为了知道**「要求完成日期」那个控件的 id**（按标签找，不写死 id）——
    # `save()` 要靠它把那个日期落进 `target_completion_date` 列。取不到就给空表，
    # 保存草稿不该因为钉钉模板读不出来而失败。
    try:
        options,_ = await template(session,self_oa_only=bool(metadata(sample).get('requires_self_oa')))
    except AppError:
        options = {'fields': []}
    await save(session,user,sample,payload,options)
    await write_audit(session,operator_id=user.id,action='update',business_type='sample',business_id=sample.id,
                      after={'dingtalk_draft':metadata(sample)},ip=client_ip(request))
    await session.commit()
    return ok(None,'草稿已保存，未发起审批')


@router.post('/samples/{sample_id}/dingtalk-submit')
async def submit(sample_id:int,payload:Draft,request:Request,user:CurrentUser=Depends(require_permission('sample:manage')),session:AsyncSession=Depends(get_db)):
    sample=await service.get_visible_or_404(session,user,sample_id,for_update=True,allow_external_flow=True)
    meta=metadata(sample)
    self_oa_enabled = self_oa_available()
    if (meta.get('requires_self_oa') or payload.mode == 'standalone') and not self_oa_enabled:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'尚未配置同序 OA 打样审批，暂不能提交审批，可先保存草稿',409)
    previous=await oa.get_by_business_key(session,inquiry_id=sample.id,inquiry_version=int(sample.version or 1),oa_type='sample') if payload.mode=='standalone' or self_oa_enabled else None
    if previous is not None and getattr(previous, 'provider', None) == 'self_oa' and previous.status in ('submitting','needs_review'):
        from app.modules.oa.service import review_stuck_submission

        await review_stuck_submission(session, previous)
        if previous.status == 'needs_review':
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "上一次 OA 发起结果待核对，请先到 OA 核对", 409)
        if payload.mode != 'standalone':
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "独立打样审批正在发起，请稍后查看进度", 409)
        return ok(oa.serialize(previous), "这张打样单正在发起 OA 审批，请稍后查看进度")
    open_new_round=bool(payload.resubmit and previous is not None and previous.status in ('rejected','withdrawn'))
    retry=False
    retry_edits=''
    if meta.get('submitted'):
        if payload.mode!=meta.get('mode') or payload.source_oa_id!=meta.get('source_oa_id'):
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED,'已经提交或关联，不能切换流程重复发单',409)
        retry=previous is not None and previous.status in ('failed','not_sent','skipped')
        if not retry and not open_new_round:
            return ok(oa.serialize(previous) if previous else None,'已提交或关联，请查看审批过程，不要重复发起')
        # 失败/未发出 = **外部确定没建单**，允许改字段后重试（B11-10，主人 2026-11-25 拍板）。
        #
        # 此前这里把"原样重发"锁成一字不改，理由是"别拿新内容覆盖已经发生过的审批快照"——
        # 对"结果未知"成立，对 failed/not_sent/skipped 不成立：那三种状态外部压根没有单子。
        # 锁住只会把人卡死：提示写着"可以修正后重试"，真改了字段却报"不能改写快照"。
        # 现在改成：允许改，快照更新为这次真正提交的内容，差异进审计。
        #
        # 比较仍只取"两边都有"的字段（`snapshot_edit_detail`）：申请人员/部门/附件
        # 由后端现算、根本不在快照里，把它们算进来会让"什么都没改"也被当成改写。
        if retry and not open_new_round:
            retry_edits = snapshot_edit_detail(meta.get("fields") or {}, payload.fields)
    # 模板**只在独立发起时取**：`save()` 要靠它知道「要求完成日期」那个控件的 id。
    # 「沿用原询价」那条路不发新的钉钉单，不需要模板 —— 而且那条路在集成测试里
    # 用的是假 session（读设置要连库），不去取也避免把测试弄红。
    options=None
    if payload.mode=='standalone':
        options,code=await template(session)
    if retry and not open_new_round:
        # 快照跟着这次真正提交的内容走；外部事实（provider/instance_id）不动。
        await remember_retry_fields(session,sample,payload,options)
    if not retry:await save(session,user,sample,payload,options)
    if payload.mode=='inquiry':
        if not payload.source_oa_id:raise AppError(ErrorCode.REQUIRED_FIELD_MISSING,'请选择原询价审批单',422)
        linked = await session.get(OaInstance, payload.source_oa_id)
        if linked is None or linked.oa_type != 'inquiry':
            raise AppError(ErrorCode.PARAM_ERROR, '原询价审批不存在，请重新选择', 422)
        if linked.status in ('rejected', 'withdrawn', 'cancelled'):
            # 关联一张没通过的审批，样品单会被永远挡在寄样/制作前，等于死单
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED,
                           '选中的原询价审批未通过，请选择已通过或在审的审批单', 422)
        before_status = sample.status
        sample.source_context={**sample.source_context,META_KEY:{**metadata(sample),'submitted':True}}
        if linked.status == 'approved':
            # 原询价已经通过：样品单应立即批准，否则它会一直卡在「待审批」，
            # 既不能登记制作也不能删除（2026-11-26 主会话 P1 修复）
            if sample.status in ('pending', 'rejected'):
                sample.status = 'approved'
                sample.approved_at = sample.approved_at or service.now()
                sample.reject_reason = None
        elif sample.status == 'rejected':
            # 驳回过、现在关联的是在审的原询价：重新进入待审批
            sample.status = 'pending'
            sample.reject_reason = None
        await service.record_opportunity_touch(session, sample, '已关联原询价审批')
        await write_audit(session,operator_id=user.id,action='update',business_type='sample',business_id=sample.id,
                         before={'status': before_status},
                         after={'linked_inquiry_oa':payload.source_oa_id,'status':sample.status},
                         ip=client_ip(request))
        await session.commit()
        return ok(None,'已关联原询价审批；打样步骤请在原同序 OA 单继续处理，本次没有新发审批')
    if self_oa_enabled:
        row,message=await oa_sample.start_sample_approval(
            session,user,sample,payload.fields,
            sample_detail_file_ids=payload.sample_detail_file_ids,
        )
        await write_audit(session,operator_id=user.id,action='update',business_type='sample',business_id=sample.id,
                         after={'oa_provider':'self_oa','oa_status':row.status,'instance_id':row.instance_id,
                                **({'retry_edits':retry_edits} if retry_edits else {})},ip=client_ip(request))
        await session.commit()
        return ok(oa.serialize(row),message)
    raise AppError(ErrorCode.STATUS_NOT_ALLOWED,
                   '独立打样审批尚未配置同序 OA 模板，暂不能提交；可先保存草稿', 409)
