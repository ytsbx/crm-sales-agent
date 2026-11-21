import HelpTip from './HelpTip'
import { TERMS, type Term } from '../help/terms'

/** 业务术语旁边的小「?」；解释文案统一取自 shared/help/terms.ts。 */
export default function TermHelp({ term }: { term: Term }) {
  return <HelpTip forLabel={term} text={TERMS[term]} />
}
