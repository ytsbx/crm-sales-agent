import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Select, Table, Tag, Toast, TextArea } from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import { listCustomers } from '../../shared/api/customer'
import {
  convertProductInsight,
  createProductInsight,
  listInsightRounds,
  listProductInsights,
  reviewProductInsight,
  submitProductInsight,
  updateProductInsight,
  type InsightConvertResult,
  type InsightRound,
  type ProductInsightRow,
} from '../../shared/api/insight'
import { optionMatcher, withCode } from '../../shared/components/optionMatch'
import { otherOption } from '../../shared/components/otherOption'

const SOURCES = ['展会', '1688/阿里', '客户反馈', '竞品调研', '社媒', '供应商推荐', '其他']
const STATUS_TONE: Record<string, 'grey' | 'orange' | 'green' | 'red' | 'blue'> = {
  draft: 'grey',
  under_review: 'orange',
  approved: 'green',
  rejected: 'red',
  converted: 'blue',
}

const EMPTY_FORM = {
  title: '',
  source: undefined as string | undefined,
  target_customer: '',
  direction: '',
  selling_points: '',
  price_assumption: '',
  conclusion: '',
  // 参考图 URL，一行一个（§6.1(6)：这个字段一直有，但四个环节都没打通）
  images: '',
}

/** 参考图：一行一个 URL → 数组。空行去掉，避免存一堆空串。 */
function parseImages(text: string): string[] | null {
  const list = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  return list.length ? list : null
}

/**
 * 逐轮快照里的内部字段名 → 中文（R08）。
 * 和核价历史那次同一个教训：审计/快照里存的是英文内部名，
 * 直接渲染出来就是「title: xxx」这种机器话，用户看不懂。
 */
const ROUND_CONTENT_LABEL: Record<string, string> = {
  title: '标题',
  source: '市场来源',
  target_customer: '目标客户',
  direction: '产品方向',
  selling_points: '假设卖点',
  price_assumption: '价格假设',
  conclusion: '结论',
}

/** 时间戳转成人看的格式。 */
function fmtTime(value: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

/** 快照里的值转成能看的字：空值说「未填」，而不是显示 null。 */
function fmtValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '（未填）'
  return String(value)
}

/** 新品洞察（§3.3 第三类：运营日常选品 → 评审 → 转需求）。 */
export default function ProductInsightsPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManage = can('product:manage')
  // 评审人按**权限码**判（第五批 §6.3 已定口径）：产品/开发岗也有 product:review，
  // 不再等于"主管"。原来写死主管角色，想让产品岗评审就得给他开主管角色。
  const canReview = can('product:review')

  const [status, setStatus] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)

  const query = useQuery({
    queryKey: ['product-insights', { status, keyword, page }],
    queryFn: () => listProductInsights({ status, keyword, page, page_size: 20 }),
  })

  // 审核历史（R08）：列表那一行只有**最后一轮**的意见与结果，
  // 「每一轮报了什么、谁提交的、谁批的、什么时候」只有落表的逐轮记录才有。
  // 点开某一条才去拉，不用给列表每行都发一个请求。
  const [historyTarget, setHistoryTarget] = useState<ProductInsightRow | null>(null)
  const roundsQuery = useQuery({
    queryKey: ['insight-rounds', historyTarget?.id],
    queryFn: () => listInsightRounds(historyTarget!.id),
    enabled: Boolean(historyTarget),
  })

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['product-insights'] })

  const [editVisible, setEditVisible] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)

  // 市场来源里的「其他」：选中它时下面多一个输入框写具体是什么
  // （不填就保持「其他」）。规则见 shared/components/otherOption.ts。
  // 空值这里转成 undefined，沿用原来的"空字符串一律发 null"口径。
  const sourceOption = otherOption({
    options: SOURCES,
    value: form.source,
    onChange: (v) => setForm({ ...form, source: v || undefined }),
  })

  const openCreate = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setEditVisible(true)
  }
  const openEdit = (row: ProductInsightRow) => {
    setEditingId(row.id)
    setForm({
      title: row.title,
      source: row.source ?? undefined,
      target_customer: row.target_customer ?? '',
      direction: row.direction ?? '',
      selling_points: row.selling_points ?? '',
      price_assumption: row.price_assumption != null ? String(row.price_assumption) : '',
      conclusion: row.conclusion ?? '',
      images: (row.images ?? []).join('\n'),
    })
    setEditVisible(true)
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      // 空字符串一律发 null：`''` 与"没填"在业务上是同一件事，
      // 存进去以后按空串渲染会出现"填过但看不出内容"的行。
      // **传 null 才能真正清空**（§6.2：更新里原来用 `if value is not None` 判断，
      // 传 null 被跳过，界面上清空了、库里旧值还在）。
      const payload = {
        title: form.title,
        source: form.source ?? null,
        target_customer: form.target_customer || null,
        direction: form.direction || null,
        selling_points: form.selling_points || null,
        price_assumption: form.price_assumption ? Number(form.price_assumption) : null,
        conclusion: form.conclusion || null,
        images: parseImages(form.images),
      }
      return editingId
        ? updateProductInsight(editingId, payload)
        : createProductInsight(payload)
    },
    onSuccess: (row, _vars) => {
      // 已通过后改关键内容会被后端退回待评审——必须说出来，
      // 否则用户只会看到状态自己变了，以为系统出错。
      Toast.success(
        row.status === 'under_review' && editingId
          ? '已保存；因修改了关键内容，已退回「待评审」需重新评审'
          : '已保存',
      )
      setEditVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const submitMutation = useMutation({
    mutationFn: (id: number) => submitProductInsight(id),
    onSuccess: (row) => {
      Toast.success(`已提交评审（第 ${row.review_round} 轮）`)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 评审弹窗（§6.1(7)：意见原来写死成"方向不明确，暂缓"，等于没评审）----
  const [reviewTarget, setReviewTarget] = useState<ProductInsightRow | null>(null)
  const [reviewApprove, setReviewApprove] = useState(true)
  const [reviewNote, setReviewNote] = useState('')

  const openReview = (row: ProductInsightRow, approve: boolean) => {
    setReviewTarget(row)
    setReviewApprove(approve)
    setReviewNote('')
  }
  const reviewMutation = useMutation({
    mutationFn: () =>
      reviewProductInsight(reviewTarget!.id, {
        approve: reviewApprove,
        note: reviewNote.trim() || null,
      }),
    onSuccess: () => {
      Toast.success(reviewApprove ? '已通过' : '已否决')
      setReviewTarget(null)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 转换弹窗（§6.1(7)：原来点一下就转、只弹个编号，看不到转了什么）----
  const [convertTarget, setConvertTarget] = useState<ProductInsightRow | null>(null)
  const [convertCustomerId, setConvertCustomerId] = useState<number | undefined>()
  const [convertKeyword, setConvertKeyword] = useState('')
  const [convertForm, setConvertForm] = useState({ quantity: '', target_price: '', remark: '' })
  const [convertKey, setConvertKey] = useState('')
  const [convertResult, setConvertResult] = useState<InsightConvertResult | null>(null)

  const convertCustomers = useQuery({
    queryKey: ['insight-convert-customers', convertKeyword],
    queryFn: () => listCustomers({ keyword: convertKeyword, page: 1, page_size: 20 }),
    enabled: Boolean(convertTarget),
  })

  const openConvert = (row: ProductInsightRow) => {
    setConvertTarget(row)
    setConvertCustomerId(undefined)
    setConvertKeyword('')
    setConvertForm({ quantity: '', target_price: '', remark: '' })
    // 幂等键：同一张弹窗里的重复提交带同一个值，网络重试不会建出第二条需求
    setConvertKey(
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}`,
    )
  }
  const convertMutation = useMutation({
    mutationFn: () =>
      convertProductInsight(convertTarget!.id, {
        customer_id: convertCustomerId ?? null,
        quantity: convertForm.quantity ? Number(convertForm.quantity) : null,
        target_price: convertForm.target_price ? Number(convertForm.target_price) : null,
        remark: convertForm.remark || null,
        request_key: convertKey,
      }),
    onSuccess: (data) => {
      // 不弹"已成功"就完事：要把生成的是哪一条、去了哪里说清楚，并给得出去
      setConvertTarget(null)
      setConvertResult(data)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <div className="page-container">
      <SectionCard>
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            运营选品沉淀：市场来源 → 产品方向 → 评审通过 → 转需求。
            价格只是内部假设，未经确认不会成为正式指导价
          </span>
          <div style={{ flex: 1 }} />
          <Input
            style={{ width: 200 }}
            placeholder="搜标题"
            value={keyword}
            onChange={setKeyword}
            onEnterPress={() => setPage(1)}
          />
          <Select
            style={{ width: 130 }}
            placeholder="状态"
            showClear
            value={status}
            onChange={(v) => {
              setStatus(v as string | undefined)
              setPage(1)
            }}
            optionList={[
              { value: 'draft', label: '记录中' },
              { value: 'under_review', label: '待评审' },
              { value: 'approved', label: '已通过' },
              { value: 'rejected', label: '已否决' },
              { value: 'converted', label: '已转需求' },
            ]}
          />
          {canManage && (
            <Button theme="solid" onClick={openCreate}>
              记录新品洞察
            </Button>
          )}
        </div>
        <Table<ProductInsightRow>
          columns={[
            {
              title: '产品方向',
              dataIndex: 'title',
              render: (text: string, record: ProductInsightRow) => (
                <div>
                  <div style={{ fontWeight: 600 }}>{text}</div>
                  {record.selling_points && (
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      假设卖点：{record.selling_points}
                    </div>
                  )}
                  {record.images && record.images.length > 0 && (
                    <div style={{ fontSize: 12, display: 'inline-flex', gap: 6 }}>
                      参考图：
                      {record.images.slice(0, 3).map((url, i) => (
                        <a key={url} href={url} target="_blank" rel="noreferrer">
                          {i + 1}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              ),
            },
            { title: '来源', dataIndex: 'source', width: 100, render: (v: string | null) => v ?? '-' },
            {
              title: '目标客户',
              dataIndex: 'target_customer',
              width: 120,
              render: (v: string | null) => v ?? '-',
            },
            {
              title: '价格假设',
              dataIndex: 'price_assumption',
              width: 100,
              render: (v: number | null) =>
                v == null ? '-' : <span style={{ color: 'var(--crm-text-3)' }}>¥{v}（假设）</span>,
            },
            { title: '负责人', dataIndex: 'owner_name', width: 80, render: (v: string | null) => v ?? '-' },
            {
              title: '状态',
              width: 120,
              render: (_: unknown, record: ProductInsightRow) => (
                <div>
                  <Tag color={STATUS_TONE[record.status] ?? 'grey'}>{record.status_label}</Tag>
                  {/* 轮次：驳回过几次、现在是第几轮，一眼看得出（§6.1(1)） */}
                  {(record.review_round ?? 1) > 1 && (
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      第 {record.review_round} 轮
                    </div>
                  )}
                  {record.review_note && (
                    <div
                      style={{ fontSize: 12, color: 'var(--crm-text-3)' }}
                      title={record.review_note}
                    >
                      意见：{record.review_note.slice(0, 10)}
                      {record.review_note.length > 10 ? '…' : ''}
                    </div>
                  )}
                </div>
              ),
            },
            {
              title: '操作',
              width: 230,
              render: (_: unknown, record: ProductInsightRow) => {
                // 逐条可操作性（R08）：后端随每一行下发"这条归不归我管""我现在能不能评"。
                // 原来只按权限码判，于是**数据范围外**的那几条也长着「编辑 / 提交评审 /
                // 转需求」，点下去才 403 —— 看着能点、其实是坏的。
                // 用 `??` 而不是 `||`：can_edit=false 是有效结论，不能被当成"没值"。
                // 老数据没有这两个字段时退回原判据，行为不变。
                const editable = canManage && (record.can_edit ?? true)
                const reviewable =
                  record.can_review ?? (canReview && record.status === 'under_review')
                return (
                  <span style={{ display: 'inline-flex', gap: 10, flexWrap: 'wrap' }}>
                    {editable && record.status !== 'converted' && record.status !== 'under_review' && (
                      <a onClick={() => openEdit(record)}>编辑</a>
                    )}
                    {editable && (record.status === 'draft' || record.status === 'rejected') && (
                      <a onClick={() => submitMutation.mutate(record.id)}>提交评审</a>
                    )}
                    {reviewable && (
                      <>
                        <a style={{ color: 'var(--crm-success)' }} onClick={() => openReview(record, true)}>
                          通过
                        </a>
                        <a style={{ color: 'var(--crm-error)' }} onClick={() => openReview(record, false)}>
                          否决
                        </a>
                      </>
                    )}
                    {editable && record.status === 'approved' && (
                      <a onClick={() => openConvert(record)}>转需求</a>
                    )}
                    {/* 审核历史：能看见这一页的人就能看它的评审史（后端读入口同权限） */}
                    <a
                      style={{ color: 'var(--crm-text-2)' }}
                      onClick={() => setHistoryTarget(record)}
                    >
                      审核历史
                    </a>
                    {/* 已转：给得出去的深链，不是一串死编号（§6.1(7)） */}
                    {record.converted_inquiry_id && (
                      <Link to="/inquiries?keyword=" style={{ color: 'var(--crm-text-3)' }}>
                        已转需求 #{record.converted_inquiry_id}
                      </Link>
                    )}
                  </span>
                )
              },
            },
          ]}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          pagination={{
            currentPage: page,
            pageSize: 20,
            total: query.data?.total ?? 0,
            onPageChange: setPage,
          }}
          empty="还没有新品洞察——运营看到好方向就记进来"
        />
      </SectionCard>

      <Modal
        title={editingId ? '编辑新品洞察' : '记录新品洞察'}
        visible={editVisible}
        onCancel={() => setEditVisible(false)}
        onOk={() => saveMutation.mutate()}
        confirmLoading={saveMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={620}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div>
            <FormLabel required>产品方向标题</FormLabel>
            <Input
              placeholder="一句话说清这个方向是什么"
              value={form.title}
              onChange={(v) => setForm({ ...form, title: v })}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 160px), 1fr))', gap: 10 }}>
            <div>
              <FormLabel>市场来源</FormLabel>
              <Select
                style={{ width: '100%' }}
                placeholder="选择来源"
                showClear
                value={sourceOption.selectValue}
                onChange={sourceOption.onSelect}
                optionList={SOURCES.map((s) => ({ value: s, label: s }))}
              />
              {sourceOption.showInput && (
                <Input
                  style={{ marginTop: 8 }}
                  placeholder="请说明具体来源（可不填）"
                  value={sourceOption.inputValue}
                  onChange={sourceOption.onInput}
                />
              )}
            </div>
            <div>
              <FormLabel>目标客户</FormLabel>
              <Input
                placeholder="面向哪类客户"
                value={form.target_customer}
                onChange={(v) => setForm({ ...form, target_customer: v })}
              />
            </div>
            <div>
              <FormLabel hint="仅内部参考">价格假设</FormLabel>
              <Input
                placeholder="如：8.5"
                value={form.price_assumption}
                onChange={(v) => setForm({ ...form, price_assumption: v })}
              />
            </div>
          </div>
          <div>
            <FormLabel>产品方向</FormLabel>
            <TextArea rows={2} placeholder="做给谁、解决什么" value={form.direction} onChange={(v) => setForm({ ...form, direction: v })} />
          </div>
          <div>
            <FormLabel>假设卖点</FormLabel>
            <TextArea rows={2} value={form.selling_points} onChange={(v) => setForm({ ...form, selling_points: v })} />
          </div>
          <div>
            <FormLabel>评估结论</FormLabel>
            <TextArea rows={2} placeholder="能否开发、卡在哪" value={form.conclusion} onChange={(v) => setForm({ ...form, conclusion: v })} />
          </div>
          <div>
            <FormLabel hint="可不填">参考图 URL</FormLabel>
            <TextArea
              rows={2}
              placeholder="一行一个"
              value={form.images}
              onChange={(v) => setForm({ ...form, images: v })}
            />
          </div>
        </div>
      </Modal>

      {/* 评审：结论 + 真实意见。否决必须写意见——否则提出者只知道"没过"，
          不知道该改什么（后端也会拦） */}
      <Modal
        title={`评审：${reviewTarget?.title ?? ''}`}
        visible={Boolean(reviewTarget)}
        onCancel={() => setReviewTarget(null)}
        onOk={() => reviewMutation.mutate()}
        confirmLoading={reviewMutation.isPending}
        okText={reviewApprove ? '确认通过' : '确认否决'}
        cancelText="取消"
        width={520}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          {(reviewTarget?.review_round ?? 1) > 1 && (
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              这是第 {reviewTarget?.review_round} 轮评审；上一轮意见：
              {reviewTarget?.review_note || '（无）'}
            </div>
          )}
          <Select
            value={reviewApprove ? 'approve' : 'reject'}
            onChange={(v) => setReviewApprove(v === 'approve')}
            optionList={[
              { value: 'approve', label: '通过' },
              { value: 'reject', label: '否决' },
            ]}
          />
          <div>
            <div style={{ marginBottom: 4 }}>
              评审意见{reviewApprove ? '（选填）' : '（否决必填，说明该改什么）'}
            </div>
            <TextArea
              rows={3}
              value={reviewNote}
              onChange={setReviewNote}
              maxCount={255}
              placeholder={reviewApprove ? '例如：方向可行，先找两家客户试' : '例如：目标客户不明确，先补客户画像'}
            />
          </div>
        </div>
      </Modal>

      {/* 转换：先说清"不选客户会变成什么"，再让人确认 */}
      <Modal
        title={`转需求：${convertTarget?.title ?? ''}`}
        visible={Boolean(convertTarget)}
        onCancel={() => setConvertTarget(null)}
        onOk={() => convertMutation.mutate()}
        confirmLoading={convertMutation.isPending}
        okText="确认转换"
        cancelText="取消"
        width={600}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            {convertCustomerId
              ? '已选客户 → 转成正常的客户询价，会校验客户是不是你的。'
              : '没选客户 → 转成「内部开发需求」：明确标注来源是市场研究，'
                + '不冒充「客户已提出采购需求」，而且不会因为没挂客户就变成人人可见。'}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>客户（可不选）</div>
            <Select
              style={{ width: '100%' }}
              placeholder="搜客户名称（不选就是内部开发需求）"
              filter={optionMatcher}
              remote
              showClear
              loading={convertCustomers.isFetching}
              onSearch={setConvertKeyword}
              value={convertCustomerId}
              onChange={(v) => setConvertCustomerId(v as number | undefined)}
              optionList={(convertCustomers.data?.items ?? []).map((c) => ({
                value: c.id,
                label: withCode(c.name, c.id),
              }))}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))', gap: 10 }}>
            <Input
              placeholder="数量（可留空）"
              value={convertForm.quantity}
              onChange={(v) => setConvertForm({ ...convertForm, quantity: v })}
            />
            <Input
              placeholder="目标价（可留空）"
              value={convertForm.target_price}
              onChange={(v) => setConvertForm({ ...convertForm, target_price: v })}
            />
          </div>
          <Input
            placeholder="补充备注（可留空）"
            value={convertForm.remark}
            onChange={(v) => setConvertForm({ ...convertForm, remark: v })}
          />
          <div style={{ background: 'var(--crm-fill, #f7f7f7)', padding: 10, borderRadius: 6, fontSize: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>会一并带过去的内容</div>
            <div>市场来源：{convertTarget?.source || '（未填）'}</div>
            <div>目标客户（洞察记录）：{convertTarget?.target_customer || '（未填）'}</div>
            <div>产品方向：{convertTarget?.direction || '（未填）'}</div>
            <div>假设卖点：{convertTarget?.selling_points || '（未填）'}</div>
            <div>
              价格假设：
              {convertTarget?.price_assumption != null
                ? `¥${convertTarget.price_assumption}（未确认，仅供内部参考）`
                : '（未填）'}
            </div>
          </div>
        </div>
      </Modal>

      {/* 转换结果：给编号 + 能点进去，不是一串死编号 */}
      <Modal
        title="已转成需求"
        visible={Boolean(convertResult)}
        onCancel={() => setConvertResult(null)}
        footer={
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <Button onClick={() => setConvertResult(null)}>知道了</Button>
            {convertResult && (
              <Link to={`/inquiries?keyword=${encodeURIComponent(convertResult.inquiry_no ?? '')}`}>
                <Button theme="solid">去看这条需求</Button>
              </Link>
            )}
          </div>
        }
        width={460}
      >
        {convertResult && (
          <div style={{ display: 'grid', gap: 6 }}>
            <div>
              来源类型：<Tag color={convertResult.origin === 'internal_dev' ? 'orange' : 'blue'}>
                {convertResult.origin_label ?? convertResult.origin}
              </Tag>
            </div>
            <div>需求编号：{convertResult.inquiry_no ?? `#${convertResult.inquiry_id}`}</div>
            {convertResult.origin === 'internal_dev' && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                这是内部开发需求，不是客户提出的采购需求 —— 对外报价、承诺交期前先确认客户。
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* 审核历史（R08）：逐轮记录，从最早一轮往下排。
          列表那一行只有**最后一轮**的值，看不出"驳回过几次、每次是什么意见、
          改完又报了什么" —— 那些只有落表的逐轮记录才回答得了。 */}
      <Modal
        title={`审核历史：${historyTarget?.title ?? ''}`}
        visible={Boolean(historyTarget)}
        onCancel={() => setHistoryTarget(null)}
        footer={<Button onClick={() => setHistoryTarget(null)}>关闭</Button>}
        width={620}
      >
        {roundsQuery.isLoading && (
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>加载中…</div>
        )}
        {!roundsQuery.isLoading && (roundsQuery.data ?? []).length === 0 && (
          <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
            还没有评审记录。提交评审之后，每一轮报了什么、谁批的都会逐条留在这里。
          </div>
        )}
        <div style={{ display: 'grid', gap: 10 }}>
          {(roundsQuery.data ?? []).map((round: InsightRound) => {
            const pending = !round.review_result
            return (
              <div
                key={round.round}
                style={{
                  border: '1px solid var(--crm-surface-high)',
                  borderRadius: 'var(--crm-radius-sm)',
                  padding: '10px 12px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>第 {round.round} 轮</span>
                  <Tag color={pending ? 'orange' : round.review_result === 'approved' ? 'green' : 'red'}>
                    {pending ? '待评审' : (round.review_result_label ?? round.review_result)}
                  </Tag>
                  <div style={{ flex: 1 }} />
                  <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                    提交：{round.submitted_by_name ?? '—'} · {fmtTime(round.submitted_at)}
                  </span>
                </div>

                {/* 这一轮报上去的内容快照：过了几轮之后要能回答"当时批的是哪一版" */}
                {round.content && (
                  <div
                    style={{
                      display: 'grid',
                      gap: 2,
                      fontSize: 12,
                      color: 'var(--crm-text-2)',
                      marginBottom: round.review_note || round.reviewed_at ? 8 : 0,
                    }}
                  >
                    {Object.entries(ROUND_CONTENT_LABEL).map(([key, label]) =>
                      key in round.content! ? (
                        <div key={key}>
                          {label}：{fmtValue(round.content![key])}
                        </div>
                      ) : null,
                    )}
                  </div>
                )}

                {round.review_note && (
                  <div style={{ fontSize: 12, color: 'var(--crm-text-2)', marginBottom: 4 }}>
                    评审意见：{round.review_note}
                  </div>
                )}
                {round.reviewed_at && (
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                    评审：{round.reviewer_name ?? '—'} · {fmtTime(round.reviewed_at)}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </Modal>
    </div>
  )
}
