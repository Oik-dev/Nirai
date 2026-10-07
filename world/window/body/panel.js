// 体を試す操作（窓を ?body 付きで開いたときだけ）。身振り・覚えた動き・表情・声を、本人の選びを待たずに体へ渡す。
export function mountBodyPanel(body, invalidate) {
  const panel = document.createElement('section');
  panel.id = 'bodyPanel';
  panel.setAttribute('aria-label', '体を試す');
  const status = document.createElement('span');
  status.setAttribute('role', 'status');
  const button = (label, action) => {
    const element = document.createElement('button');
    element.type = 'button';
    element.textContent = label;
    element.addEventListener('click', () => {
      status.textContent = '';
      Promise.resolve(action()).catch(error => { status.textContent = error.message; }).finally(invalidate);
    });
    panel.append(element);
  };
  for (const name of body.gestures) button(name, () => body.play(name));

  const motion = document.createElement('input');
  motion.placeholder = '覚えた動きの名前';
  motion.setAttribute('aria-label', '覚えた動きの名前');
  panel.append(motion);
  button('動く', () => body.play(motion.value.trim()));

  const expression = document.createElement('select');
  expression.setAttribute('aria-label', '表情');
  for (const name of ['', ...body.expressions]) expression.append(new Option(name || '表情なし', name));
  expression.addEventListener('change', () => { body.setExpression(expression.value || null); invalidate(); });
  panel.append(expression);

  button('話しかける', () => body.hear('Master', ''));
  button('話す', () => body.hear('Serina', 'あ'.repeat(60)));
  panel.append(status);
  document.body.append(panel);
}
