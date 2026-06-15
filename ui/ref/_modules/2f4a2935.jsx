// shared.jsx — Orb, Composer, and the canned AI engine shared across screens

// ── Orb ───────────────────────────────────────────────────────────────────
function Orb({ size = 96 }) {
  return (
    <div className="orb" style={{ width: size, height: size }}>
      <div className="orb__glow" />
      <div className="orb__ball" />
      <div className="orb__hi" />
    </div>
  );
}

// ── Composer ────────────────────────────────────────────────────────────────
function Composer({ value, onChange, onSend, variant = 'hero', placeholder, citation, onToggleCitation, style: styleVal, onStyle }) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }, [value]);

  const submit = () => {
    const v = value.trim();
    if (v) onSend(v);
  };
  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className={'composer composer--' + variant}>
      <div className="composer__top">
        <IconSparkle size={18} className="composer__spark" />
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder={placeholder || 'Спросите МСБ-Ассистента…'}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKey}
        />
      </div>
      <div className="composer__bar">
        <div className="composer__left">
          <button className="composer__tool" title="Прикрепить файл"><IconAttach size={17} /><span>Документ</span></button>
          <button className="composer__tool composer__tool--icon" title="Изображение"><IconImage size={17} /></button>
          <button className="composer__tool composer__tool--style" title="Стиль ответа">
            {styleVal || 'Деловой'} <IconChevron size={14} />
          </button>
        </div>
        <div className="composer__right">
          {onToggleCitation && (
            <button
              className={'composer__cite' + (citation ? ' is-on' : '')}
              onClick={onToggleCitation}
              title="Ссылки на источники"
            >
              <span className="composer__switch"><i /></span>
              Источники
            </button>
          )}
          <button className="composer__send" onClick={submit} aria-label="Отправить">
            <IconSend size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Canned AI engine ─────────────────────────────────────────────────────────
function generateResponse(text) {
  const q = text.toLowerCase();
  if (/(докум|справк|что нужно|какие нужн)/.test(q)) {
    return {
      delay: 1100,
      blocks: [
        { type: 'p', text: 'Для подачи заявки на кредит для ООО потребуется базовый пакет документов. Большая часть подтянется автоматически из подключённых систем — отмечу галочками.' },
        { type: 'list', items: [
          { t: 'Бухгалтерская отчётность за 4 квартала', done: true },
          { t: 'Выписка по расчётному счёту за 6 месяцев', done: true },
          { t: 'Устав и решение о назначении директора', done: false },
          { t: 'Справка об отсутствии задолженности по налогам', done: false },
        ] },
        { type: 'note', text: 'Готово 2 из 4. Загрузите оставшиеся документы — и я проверю их перед отправкой.' },
      ],
    };
  }
  if (/(рассчита|платеж|платёж|график|переплат|ежемес)/.test(q)) {
    return {
      delay: 1200,
      blocks: [
        { type: 'p', text: 'Рассчитал по программе «Оборот+» на основе вашего среднемесячного оборота. Вот предварительные параметры:' },
        { type: 'stats', items: [
          { k: 'Сумма', v: '5 000 000 ₽' },
          { k: 'Срок', v: '24 мес.' },
          { k: 'Ставка', v: '14,5%' },
          { k: 'Платёж / мес.', v: '240 600 ₽' },
        ] },
        { type: 'p', text: 'Итоговая переплата за весь срок — около 774 400 ₽. Долговая нагрузка остаётся в комфортных 18% от оборота.' },
        { type: 'note', text: 'Изменить сумму или срок? Скажите параметры — пересчитаю мгновенно.' },
      ],
    };
  }
  if (/(скоринг|кредитоспособ|шанс|одобрен|оцен|рейтинг)/.test(q)) {
    return {
      delay: 1300,
      blocks: [
        { type: 'p', text: 'Провёл экспресс-оценку ООО «Северторг» по 14 факторам. Кредитный рейтинг компании:' },
        { type: 'score', value: 78, label: 'Высокая вероятность одобрения' },
        { type: 'list', items: [
          { t: 'Стабильный оборот с положительной динамикой +12% за год', done: true },
          { t: 'Отсутствие просрочек по действующим обязательствам', done: true },
          { t: 'Низкая текущая долговая нагрузка', done: true },
          { t: 'Рекомендую увеличить долю собственного капитала', done: false },
        ] },
        { type: 'note', text: 'С таким профилем доступны программы со ставкой от 13,9%. Подобрать предложения?' },
      ],
    };
  }
  // default → product recommendation
  return {
    delay: 1300,
    blocks: [
      { type: 'p', text: 'Подобрал три программы под профиль вашего бизнеса — торговля, оборот ~28 млн ₽/год. Сравните ключевые параметры:' },
      { type: 'products', items: [
        { name: 'Оборот+', rate: 'от 14,5%', sum: 'до 7 млн ₽', term: '36 мес.', tag: 'Рекомендуем', note: 'Без залога, решение за 1 день' },
        { name: 'Инвест-кредит', rate: 'от 13,9%', sum: 'до 30 млн ₽', term: '60 мес.', tag: '', note: 'Под развитие и оборудование' },
        { name: 'Экспресс-овердрафт', rate: 'от 16,2%', sum: 'до 3 млн ₽', term: '12 мес.', tag: '', note: 'Лимит на расчётном счёте' },
      ] },
      { type: 'note', text: 'Программа «Оборот+» лучше всего подходит под ваши цели. Оформить заявку или рассчитать платёж?' },
    ],
  };
}

Object.assign(window, { Orb, Composer, generateResponse });
