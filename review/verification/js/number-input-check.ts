import { parseOptionalNumber } from '../../../frontend/src/shared/number-input'

const bad = parseOptionalNumber('abc')
console.log('[F11-01] parseOptionalNumber("abc") =', bad, 'isNaN=', Number.isNaN(bad))
console.log('[F11-01] JSON.stringify({quantity: NaN}) =', JSON.stringify({ quantity: bad }))

const big = parseOptionalNumber('9007199254740993')
console.log('[F11-03] Number("9007199254740993") =', big, '回写文本=', String(big))
const price = parseOptionalNumber('900719925474.0993')
console.log('[F11-03] Number("900719925474.0993") =', price, '回写文本=', String(price), '原值=', '900719925474.0993')
