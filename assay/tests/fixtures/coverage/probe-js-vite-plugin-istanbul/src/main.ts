import { add, subtract } from './math'

const output = document.getElementById('output')
const total = add(2, 3) + subtract(10, 4)
if (output) {
  output.textContent = String(total)
}
