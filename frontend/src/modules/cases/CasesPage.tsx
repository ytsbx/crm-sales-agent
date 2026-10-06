import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Banner,
  Button,
  Input,
  Modal,
  Select,
  Table,
  Tag,
  Toast,
  TextArea,
} from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import { useAuthStore } from '../../shared/store/auth'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import {
  createCase,
  getCase,
  listCases,
  reviseCase,
  reviewCase,
  submitCase,
  updateCase,
  type CaseRow,
} from '../../shared/api/cases'
import { listQuotes } from '../../shared/api/quote'
import { listOrders } from '../../shared/api/order'
import { listSamples } from '../../shared/api/sample'
import { listCustomers } from '../../shared/api/customer'

const STAGES = ['understanding', 'quote', 'sample', 'first_order', 'repeat', 'stable']
const STAGE_LABEL: Record<string, string> = {
  understanding: '了解', quote: '报价', sample: '打样',
  first_order: '首单', repeat: '返单', stable: '稳定复购',
}
const STATUS_TONE: Record<string, 'green' | 'grey' | 'orange' | 'red'> = {
  published: 'green', draft: 'grey', pending_review: 'orange', rejected: 'red',
  // 被修订版取代的旧版：**仍可读、只是不能再改**（§5.1.5），所以是中性灰不是"错"的红
  superseded: 'grey',
}

const EMPTY_FORM = {
  title: '',
  customer_id: undefined as number | undefined,
  quote_id: undefined as number | undefined,
  order_id: undefined as number | undefined,
  sample_id: undefined as number | undefined,
  customer_label: '',
  industry: '',
  product_line: '',
  stage_reached: undefined as string | undefined,
  problem_tags: '',
  background: '',
  goal: '',
  key_actions: '',
  objection_handling: '',
  process: '',
  result: '',
  lessons: '',
}

export default function CasesPage() {
  const queryClient = useQueryClient()
  const { can, isReviewer } = usePermissions()
  // 「开修订稿」后端只放行**作者或主管**（其余人 403）。前端据此显隐按钮，
  // 免得每个人点一下都被拒——真正的拦截仍在后端。
  const currentUserId = useAuthStore((state) => state.user?.id)
  const [filters, setFilters] = useState({
    keyword: '',
    status: undefined as string | undefined,
    // 第四批 §5.1.6：下面这几个筛选后端早就支持（标签之外），此前只是前端没接通，
    // 看起来像"只能按关键词和状态搜"。
    industry: undefined as string | undefined,
    product_line: undefined as string | undefined,
    stage: undefined as string | undefined,
    problem_tags: undefined as string | undefined,
  })
  const [page, setPage] = useState(1)
  const [includeHistory, setIncludeHistory] = useState(false)

  // 改筛选条件要回到第 1 页：否则筛完还停在第 5 页，翻出来是空的，
  // 看着像"没有符合条件的案例"，其实只是页码越界了。
  const applyFilter = (patch: Partial<typeof filters>) => {
    setFilters((current) => ({ ...current, ...patch }))
    setPage(1)
  }

  const casesQuery = useQuery({
    queryKey: ['cases', filters, page, includeHistory],
    queryFn: () =>
      listCases({ ...filters, page, page_size: 20, include_history: includeHistory }),
  })
  const customersQuery = useQuery({
    queryKey: ['case-customers'],
    queryFn: () => listCustomers({ page: 1, page_size: 200 }),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['cases'] })
  }

  const [editVisible, setEditVisible] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [detail, setDetail] = useState<CaseRow | null>(null)
  // 驳回意见改成**真弹窗填写**。原先写死一句"请补充关键动作与可复用做法"，
  // 于是不管案例哪里不合格，作者收到的都是同一句话，照着改根本不知道改什么（返工单第 7 条）。
  const [reviewModal, setReviewModal] = useState<{ visible: boolean; id?: number }>({
    visible: false,
  })
  const [reviewNote, setReviewNote] = useState('')

  // 打开详情时**重新拉一次详情接口**，而不是直接拿列表那条记录渲染。
  // 列表与详情是两套序列化，历史上就漂移过（列表标题带着客户全称、详情已换成代称），
  // 而详情直接用列表记录打开会把这种漂移放大成"分享版泄露客户身份"（§5.1.1）。
  // 先用列表记录占位避免弹窗空一下，拿到详情后覆盖。
  const openDetail = async (row: CaseRow) => {
    setDetail(row)
    try {
      const full = await getCase(row.id)
      // 期间用户可能已经关掉或换了另一条，只认仍然是同一条的那个响应
      setDetail((current) => (current && current.id === row.id ? full : current))
    } catch {
      // 拉详情失败就保持列表那份（它同样已按分享口径脱敏），不把弹窗关掉
    }
  }

  /** 只按 id 拉一条详情（用于旧版的"查看当前版本"跳转）。
   *
   * 不复用 `openDetail`：那个函数会先 `setDetail(row)`，传一个只有 id 的假对象进去，
   * 界面会先渲染一片空白、等接口回来才填上，看着像闪了一下。
   * 这里直接等结果再设，失败就明说——不静默，否则用户点了没反应更困惑。
   */
  const openDetailById = async (id: number) => {
    try {
      setDetail(await getCase(id))
    } catch {
      Toast.error('打不开这一版：可能已删除，或你没有查看权限')
    }
  }

  // 证据单据（§3.7）：选了客户才去拉这个客户的报价/订单/打样
  const evidenceCustomerId = editVisible ? form.customer_id : undefined
  const quotesQuery = useQuery({
    queryKey: ['case-quotes', evidenceCustomerId],
    queryFn: () => listQuotes({ customer_id: evidenceCustomerId, page_size: 100 }),
    enabled: Boolean(evidenceCustomerId),
  })
  const ordersQuery = useQuery({
    queryKey: ['case-orders', evidenceCustomerId],
    queryFn: () => listOrders({ customer_id: evidenceCustomerId, page_size: 100 }),
    enabled: Boolean(evidenceCustomerId),
  })
  const samplesQuery = useQuery({
    queryKey: ['case-samples', evidenceCustomerId],
    queryFn: () => listSamples({ customer_id: evidenceCustomerId, page_size: 100 }),
    enabled: Boolean(evidenceCustomerId),
  })

  const openCreate = () => {
    setEditId(null)
    setForm(EMPTY_FORM)
    setEditVisible(true)
  }
  const openEdit = (row: CaseRow) => {
    setEditId(row.id)
    setForm({
      ...EMPTY_FORM,
      title: row.title,
      customer_id: row.customer_id ?? undefined,
      quote_id: row.quote_id ?? undefined,
      order_id: row.order_id ?? undefined,
      sample_id: row.sample_id ?? undefined,
      customer_label: row.customer_label ?? '',
      industry: row.industry ?? '',
      product_line: row.product_line ?? '',
      stage_reached: row.stage_reached ?? undefined,
      problem_tags: (row.problem_tags ?? []).join('，'),
      background: row.background ?? '',
      goal: row.goal ?? '',
      key_actions: row.key_actions ?? '',
      objection_handling: row.objection_handling ?? '',
      process: row.process ?? '',
      result: row.result ?? '',
      lessons: row.lessons ?? '',
    })
    setEditVisible(true)
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        ...form,
        problem_tags: form.problem_tags
          .split(/[,，]/)
          .map((tag) => tag.trim())
          .filter(Boolean),
      }
      return editId ? updateCase(editId, payload) : createCase(payload)
    },
    onSuccess: () => {
      Toast.success('案例已保存')
      setEditVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const submitMutation = useMutation({
    mutationFn: (id: number) => submitCase(id),
    onSuccess: () => {
      Toast.success('已提交审核')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const reviewMutation = useMutation({
    // note 由调用方给：通过时留空即可，**驳回时必须是主管自己写的那句话**。
    // 原来这里写死一句「请补充关键动作与可复用做法」，不管哪里不合格都是同一句，
    // 作者照着改也不知道改什么。
    mutationFn: ({ id, approve, note }: { id: number; approve: boolean; note?: string }) =>
      reviewCase(id, { approve, note }),
    onSuccess: (result) => {
      Toast.success(result.status === 'published' ? '已发布' : '已驳回')
      setReviewModal({ visible: false })
      setReviewNote('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  // 开修订稿（§5.1.5）：已发布的那一版**只读**，要改就复制一份新的重新走审核。
  // 后端做了幂等——同一原版最多一份在途修订稿，重复点不会建出一堆 V2。
  const reviseMutation = useMutation({
    mutationFn: (id: number) => reviseCase(id),
    onSuccess: (result) => {
      Toast.success(
        `已开第 ${result.version ?? 2} 版修订稿（草稿）。原版继续可供培训；改完记得提交审核`,
      )
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <div style={{ padding: 20, maxWidth: 1200, margin: '0 auto' }}>
      <SectionCard>
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <Input
            style={{ width: 200 }}
            placeholder="搜标题 / 做法 / 关键动作"
            value={filters.keyword}
            onChange={(v) => applyFilter({ keyword: v })}
          />
          <Select
            style={{ width: 120 }}
            placeholder="状态"
            value={filters.status}
            onChange={(v) => applyFilter({ status: v as string | undefined })}
            optionList={[
              { value: 'published', label: '已发布' },
              { value: 'pending_review', label: '待审核' },
              { value: 'draft', label: '编写中' },
              { value: 'rejected', label: '已驳回' },
            ]}
            showClear
          />
          {/* 行业/产品线是自由文本，用输入框（后端按包含匹配，不用打全称） */}
          <Input
            style={{ width: 110 }}
            placeholder="行业"
            value={filters.industry ?? ''}
            onChange={(v) => applyFilter({ industry: v || undefined })}
          />
          <Input
            style={{ width: 110 }}
            placeholder="产品线"
            value={filters.product_line ?? ''}
            onChange={(v) => applyFilter({ product_line: v || undefined })}
          />
          <Select
            style={{ width: 110 }}
            placeholder="阶段"
            value={filters.stage}
            onChange={(v) => applyFilter({ stage: v as string | undefined })}
            optionList={STAGES.map((s) => ({ value: s, label: STAGE_LABEL[s] }))}
            showClear
          />
          <Input
            style={{ width: 110 }}
            placeholder="问题标签"
            value={filters.problem_tags ?? ''}
            onChange={(v) => applyFilter({ problem_tags: v || undefined })}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
            <input
              type="checkbox"
              checked={includeHistory}
              onChange={(e) => {
                setIncludeHistory(e.target.checked)
                setPage(1)
              }}
            />
            <span title="默认只列当前版本；勾上会把被修订版取代的旧版本也列出来">
              含历史版本
            </span>
          </div>
          <div style={{ flex: 1 }} />
          {can('quote:view') && (
            <Button theme="solid" onClick={openCreate}>
              新建案例
            </Button>
          )}
        </div>
        <Table<CaseRow>
          columns={[
            { title: '标题', dataIndex: 'title' },
            { title: '作者', dataIndex: 'author_name', width: 100 },
            { title: '行业', dataIndex: 'industry', width: 100, render: (v: string | null) => v ?? '-' },
            { title: '产品线', dataIndex: 'product_line', width: 100, render: (v: string | null) => v ?? '-' },
            {
              title: '阶段',
              dataIndex: 'stage_reached',
              width: 90,
              render: (v: string | null) => (v ? STAGE_LABEL[v] ?? v : '-'),
            },
            {
              title: '客户',
              width: 150,
              render: (_: unknown, record: CaseRow) => record.customer_name ?? record.customer_label ?? '-',
            },
            {
              title: '状态',
              dataIndex: 'status_label',
              width: 150,
              render: (v: string, record: CaseRow) => (
                <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                  <Tag color={STATUS_TONE[record.status] ?? 'grey'}>{v}</Tag>
                  {(record.version ?? 1) > 1 && (
                    // 第几版要看得见：列表里同时出现 V2 与原版时，得能分清哪个是新的
                    <span style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>
                      V{record.version}
                    </span>
                  )}
                </span>
              ),
            },
            {
              title: '操作',
              width: 240,
              render: (_: unknown, record: CaseRow) => (
                <span style={{ display: 'inline-flex', gap: 10 }}>
                  <a onClick={() => void openDetail(record)}>详情</a>
                  {(record.status === 'draft' || record.status === 'rejected') && (
                    <>
                      <a onClick={() => openEdit(record)}>编辑</a>
                      <a onClick={() => submitMutation.mutate(record.id)}>提交审核</a>
                    </>
                  )}
                  {/* 只有**当前发布版**能开修订稿。
                      ⚠️ 已被取代的旧版**不给**这个入口（实测确认过的坑）：
                      从 V1 开出来的草稿取的是 V1 的旧内容、版本号还会跟已发布的 V2 撞号，
                      审核通过后就是"用旧内容覆盖新版本"——把改进过的内容退回旧版。
                      旧版仍然读得到（见上面的详情弹窗），只是不能从它派生新版本。 */}
                  {record.status === 'published' &&
                    (isReviewer || record.author_id === currentUserId) && (
                      <a onClick={() => reviseMutation.mutate(record.id)}>开修订稿</a>
                    )}
                  {isReviewer && record.status === 'pending_review' && (
                    <>
                      <a style={{ color: 'var(--crm-success)' }} onClick={() => reviewMutation.mutate({ id: record.id, approve: true })}>
                        通过
                      </a>
                      <a
                        style={{ color: 'var(--crm-danger, #d45)' }}
                        onClick={() => {
                          setReviewNote('')
                          setReviewModal({ visible: true, id: record.id })
                        }}
                      >
                        驳回
                      </a>
                    </>
                  )}
                </span>
              ),
            },
          ]}
          dataSource={casesQuery.data?.items ?? []}
          loading={casesQuery.isLoading}
          rowKey="id"
          pagination={{
            currentPage: page,
            pageSize: 20,
            total: casesQuery.data?.total ?? 0,
            onPageChange: setPage,
          }}
          empty="还没有案例——把做得好的单子沉淀下来"
        />
      </SectionCard>

      <Modal
        title="驳回案例"
        visible={reviewModal.visible}
        onCancel={() => setReviewModal({ visible: false })}
        onOk={() => {
          // 理由必填：作者要照着这句话改，空着等于让他猜
          if (!reviewNote.trim()) {
            Toast.error('请写清驳回原因：作者要照着它改')
            return
          }
          reviewMutation.mutate({
            id: reviewModal.id as number,
            approve: false,
            note: reviewNote.trim(),
          })
        }}
        confirmLoading={reviewMutation.isPending}
        okText="驳回"
        cancelText="取消"
        width={520}
      >
        <div style={{ display: 'grid', gap: 8 }}>
          <FormLabel required hint="作者会看到这段话，请说清哪里不合格、要补什么">
            驳回原因
          </FormLabel>
          <TextArea
            rows={4}
            value={reviewNote}
            onChange={setReviewNote}
            placeholder="例如：关键动作只写了结果，看不到具体怎么谈的；证据单据挂错了订单"
          />
        </div>
      </Modal>

      <Modal
        title={editId ? '编辑案例' : '新建案例'}
        visible={editVisible}
        onCancel={() => setEditVisible(false)}
        onOk={() => saveMutation.mutate()}
        confirmLoading={saveMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={680}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          <div>
            <FormLabel required>标题</FormLabel>
            <Input placeholder="一句话说清这个案例做成了什么" value={form.title} onChange={(v) => setForm({ ...form, title: v })} />
          </div>
          <div>
            <FormLabel hint="看案例的人可按权限跳转过去">关联客户</FormLabel>
            <Select
              style={{ width: '100%' }}
              placeholder="选择客户"
              filter
              showClear
              value={form.customer_id}
              onChange={(v) =>
                setForm({
                  ...form,
                  customer_id: v as number | undefined,
                  // 换客户时清掉旧证据，避免挂到别的客户的单据上
                  quote_id: undefined,
                  order_id: undefined,
                  sample_id: undefined,
                })
              }
              optionList={(customersQuery.data?.items ?? []).map((c) => ({ value: c.id, label: c.name }))}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div>
              <FormLabel hint="对外展示用，别写客户真名">客户代称</FormLabel>
              <Input placeholder="如：某包装厂" value={form.customer_label} onChange={(v) => setForm({ ...form, customer_label: v })} />
            </div>
            <div>
              <FormLabel>行业</FormLabel>
              <Input placeholder="如：食品" value={form.industry} onChange={(v) => setForm({ ...form, industry: v })} />
            </div>
            <div>
              <FormLabel>产品线</FormLabel>
              <Input placeholder="如：重型包装" value={form.product_line} onChange={(v) => setForm({ ...form, product_line: v })} />
            </div>
            <div>
              <FormLabel>推进到的阶段</FormLabel>
              <Select
                style={{ width: '100%' }}
                placeholder="选择阶段"
                value={form.stage_reached}
                onChange={(v) => setForm({ ...form, stage_reached: v as string })}
                optionList={STAGES.map((s) => ({ value: s, label: STAGE_LABEL[s] }))}
                showClear
              />
            </div>
          </div>
          <div>
            <FormLabel hint="逗号分隔">问题标签</FormLabel>
            <Input placeholder="如：价格异议，交期紧" value={form.problem_tags} onChange={(v) => setForm({ ...form, problem_tags: v })} />
          </div>
          {form.customer_id && (
            <div
              style={{
                display: 'grid',
                gap: 8,
                padding: 10,
                background: 'var(--crm-surface-low)',
                borderRadius: 8,
              }}
            >
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                证据单据（§3.7：从已有时间线和单据里挑证据，看案例的人可跳转查看）
              </div>
              <div>
                <FormLabel hint="可不填">关联报价单</FormLabel>
                <Select
                  style={{ width: '100%' }}
                  placeholder="选择报价单"
                  showClear
                  value={form.quote_id}
                  onChange={(v) => setForm({ ...form, quote_id: v as number | undefined })}
                  optionList={(quotesQuery.data?.items ?? []).map((q) => ({
                    value: q.id,
                    label: `${q.quote_no} · ${q.status_label ?? q.status}`,
                  }))}
                />
              </div>
              <div>
                <FormLabel hint="可不填">关联订单</FormLabel>
                <Select
                  style={{ width: '100%' }}
                  placeholder="选择订单"
                  showClear
                  value={form.order_id}
                  onChange={(v) => setForm({ ...form, order_id: v as number | undefined })}
                  optionList={(ordersQuery.data?.items ?? []).map((o) => ({
                    value: o.id,
                    label: `${o.order_no} · ${o.status_label ?? o.status} · ¥${o.total_amount ?? 0}`,
                  }))}
                />
              </div>
              <div>
                <FormLabel hint="可不填">关联打样单</FormLabel>
                <Select
                  style={{ width: '100%' }}
                  placeholder="选择打样单"
                  showClear
                  value={form.sample_id}
                  onChange={(v) => setForm({ ...form, sample_id: v as number | undefined })}
                  optionList={(samplesQuery.data?.items ?? []).map((sp) => ({
                    value: sp.id,
                    label: `打样单 #${sp.id} · ${sp.status_label ?? sp.status}`,
                  }))}
                />
              </div>
            </div>
          )}
          <div>
            <FormLabel>客户背景</FormLabel>
            <TextArea rows={2} value={form.background} onChange={(v) => setForm({ ...form, background: v })} />
          </div>
          <div>
            <FormLabel>客户目标</FormLabel>
            <TextArea rows={2} value={form.goal} onChange={(v) => setForm({ ...form, goal: v })} />
          </div>
          {/* 下面两项**刻意不标红星**：存草稿时后端根本不要求它们，
              只有「提交审核」才要求两者至少填一项（cases/service.py 的 submit_case）。
              标成必填就是假的 —— 用 hint 把真实规则讲出来。 */}
          <div>
            <FormLabel hint="与「可复用做法」至少填一项（提交审核时校验）">关键动作</FormLabel>
            <TextArea rows={2} value={form.key_actions} onChange={(v) => setForm({ ...form, key_actions: v })} />
          </div>
          <div>
            <FormLabel>异议处理</FormLabel>
            <TextArea rows={2} value={form.objection_handling} onChange={(v) => setForm({ ...form, objection_handling: v })} />
          </div>
          <div>
            <FormLabel>报价/打样经过</FormLabel>
            <TextArea rows={2} value={form.process} onChange={(v) => setForm({ ...form, process: v })} />
          </div>
          <div>
            <FormLabel>结果</FormLabel>
            <TextArea rows={2} value={form.result} onChange={(v) => setForm({ ...form, result: v })} />
          </div>
          <div>
            <FormLabel hint="与「关键动作」至少填一项">可复用做法</FormLabel>
            <TextArea rows={2} value={form.lessons} onChange={(v) => setForm({ ...form, lessons: v })} />
          </div>
        </div>
      </Modal>

      <Modal
        title={
          <span>
            {detail?.title ?? ''}
            {/* 版本号要摆在标题上：列表里同时有 V2 和原版时，点开详情得知道自己看的是哪一版 */}
            {detail && (detail.version ?? 1) > 1 && (
              <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--crm-text-3)' }}>
                V{detail.version}
              </span>
            )}
          </span>
        }
        visible={Boolean(detail)}
        onCancel={() => setDetail(null)}
        footer={null}
        width={680}
      >
        {detail && (
          <div style={{ display: 'grid', gap: 10, fontSize: 14 }}>
            {/* 已被取代的旧版：**内容照常可读**（培训资料不断档），但必须说清
                "这是历史版本、且不能再改"，否则读者会照着过时的做法用，
                作者也会纳闷为什么编辑按钮没了（§5.1.5 的口径）。 */}
            {detail.status === 'superseded' && (
              <Banner
                type="info"
                closeIcon={null}
                description={
                  <span>
                    这是<b>历史版本</b>（V{detail.version ?? 1}），已被更新的一版取代，
                    内容保留供查阅、不能再修改。
                    {detail.superseded_by ? (
                      <>
                        {' '}
                        <a onClick={() => void openDetailById(detail.superseded_by as number)}>
                          查看当前版本
                        </a>
                      </>
                    ) : null}
                  </span>
                }
              />
            )}
            {/* 修订稿：说明它是从哪一版改出来的，免得作者忘了自己在改什么。
                已发布和在途要说**不一样**的话——对已经发布的版本说"审核通过后会替换它"，
                用户会困惑"不是已经发布了吗"。 */}
            {detail.revision_of_id && detail.status !== 'superseded' && (
              <Banner
                type="info"
                closeIcon={null}
                description={
                  detail.status === 'published' ? (
                    <span>
                      本版是第 {detail.version ?? 2} 版（从第 {(detail.version ?? 2) - 1} 版改出），
                      已替换原版成为当前版本；原版转为只读的历史版本，仍可查阅。
                    </span>
                  ) : (
                    <span>
                      本版是第 {detail.version ?? 2} 版（从第 {(detail.version ?? 2) - 1} 版改出）的
                      {detail.status === 'pending_review' ? '待审稿' : '草稿'}。
                      原版继续可供培训；这一版审核通过后会替换它为当前版本。
                    </span>
                  )
                }
              />
            )}
            {/* 脱敏可解释（§3.7）：读者要知道"这里为什么少了个数字"，
                作者/主管要在发布前知道"分享出去会被抹掉哪些片段" */}
            {detail.share_view && (detail.redaction_summary ?? []).length > 0 && (
              <Banner
                type="info"
                closeIcon={null}
                description={`分享版已脱敏：${(detail.redaction_summary ?? []).join('、')}。做法可学，具体价格与联系方式不外泄。`}
              />
            )}
            {!detail.share_view && (detail.redaction_summary ?? []).length > 0 && (
              <Banner
                type="warning"
                closeIcon={null}
                description={`本案例正文含受限信息（${(detail.redaction_summary ?? []).join('、')}），分享版会自动替换成占位。建议先把正文里的数字改写后再发布，培训价值不受影响。`}
              />
            )}
            {!detail.share_view && (detail.hidden_evidence ?? []).length > 0 && (
              <Banner
                type="warning"
                closeIcon={null}
                description="注意：这条案例关联的原始单据，部分读者没有查看权限——分享版不会向他们下发单据入口（原单据仍按业务权限访问）。"
              />
            )}
            <div>
              {detail.customer_name ?? detail.customer_label ?? '客户未填'}
              {detail.industry ? ` · ${detail.industry}` : ''}
              {detail.product_line ? ` · ${detail.product_line}` : ''}
              {detail.stage_reached ? ` · ${STAGE_LABEL[detail.stage_reached] ?? detail.stage_reached}` : ''}
            </div>
            {([
              ['客户背景', detail.background],
              ['客户目标', detail.goal],
              ['关键动作', detail.key_actions],
              ['异议处理', detail.objection_handling],
              ['报价/打样经过', detail.process],
              ['结果', detail.result],
              ['可复用做法', detail.lessons],
            ] as const)
              .filter(([, text]) => text)
              .map(([label, text]) => (
                <div key={label}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>{label}</div>
                  <div style={{ whiteSpace: 'pre-wrap', color: 'var(--crm-text-2)' }}>{text}</div>
                </div>
              ))}
            {(detail.quote_id || detail.order_id || detail.sample_id) && (
              <div style={{ display: 'flex', gap: 12, fontSize: 13, flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--crm-text-3)' }}>证据单据：</span>
                {detail.quote_id && <Link to={`/quotes/${detail.quote_id}`}>报价单 #{detail.quote_id}</Link>}
                {detail.order_id && <Link to={`/orders/${detail.order_id}`}>订单 #{detail.order_id}</Link>}
                {detail.sample_id && (
                  <Link to={`/samples/${detail.sample_id}`}>打样单 #{detail.sample_id}</Link>
                )}
              </div>
            )}
            {detail.share_view && (detail.hidden_evidence ?? []).length > 0 && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                该案例关联的原始单据对当前账号不可见——案例只引用单据，打开仍需原单据权限。
              </div>
            )}
            {(detail.problem_tags ?? []).length > 0 && (
              <div>
                {(detail.problem_tags ?? []).map((tag) => (
                  <Tag key={tag}>{tag}</Tag>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
