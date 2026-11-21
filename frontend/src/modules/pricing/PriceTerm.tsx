import TermHelp from '../../shared/components/TermHelp'

type PriceTermName = '标准价' | '指导价' | '最低保护价' | '目标利润率'

export default function PriceTerm({ term }: { term: PriceTermName }) {
  return (
    <span className="crm-help-label">
      {term}
      <TermHelp term={term} />
    </span>
  )
}
