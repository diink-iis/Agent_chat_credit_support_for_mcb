// welcome.jsx — centered greeting screen, two layout variants (A / B)

const PROMPTS_A = [
  { Icon: IconProducts, title: 'Подобрать кредитную программу', sub: 'под цели и оборот вашего бизнеса' },
  { Icon: IconCalculator, title: 'Рассчитать платёж по кредиту', sub: 'график погашения и переплата' },
  { Icon: IconDocs, title: 'Какие документы нужны для заявки', sub: 'чек-лист для вашей формы бизнеса' },
  { Icon: IconScoring, title: 'Оценить шансы на одобрение', sub: 'экспресс-скоринг по 14 факторам' },
];

const PROMPTS_B = [
  { Icon: IconProducts, title: 'Подбор кредита', sub: 'Программа под цели, оборот и сезонность вашего бизнеса.' },
  { Icon: IconCalculator, title: 'Расчёт платежа', sub: 'Точный график погашения, ставка и итоговая переплата.' },
  { Icon: IconScoring, title: 'Скоринг', sub: 'Оценка кредитоспособности и вероятности одобрения.' },
];

function greeting() {
  const h = new Date().getHours();
  if (h < 6) return 'Доброй ночи';
  if (h < 12) return 'Доброе утро';
  if (h < 18) return 'Добрый день';
  return 'Добрый вечер';
}

function WelcomeScreen({ variant, draft, setDraft, onSend, citation, setCitation }) {
  const composer = (
    <Composer
      value={draft}
      onChange={setDraft}
      onSend={onSend}
      variant="hero"
      placeholder="Спросите про кредит, платёж или документы…"
      citation={citation}
      onToggleCitation={() => setCitation((c) => !c)}
    />
  );

  if (variant === 'B') {
    return (
      <div className="welcome welcome--b">
        <div className="welcome__inner">
          <Orb size={72} />
          <h1 className="welcome__title">{greeting()}, Алексей!</h1>
          <p className="welcome__sub">Ваш помощник по кредитованию бизнеса — анализ, расчёты и подбор программ в одном месте.</p>

          <button className="modelcard">
            <span className="modelcard__ic"><IconSparkle size={18} /></span>
            <span className="modelcard__txt">
              <strong>Работа с МСБ&nbsp;GPT-5 · аналитика в реальном времени</strong>
              <small>Доступ к вашим оборотам, отчётности и каталогу банковских продуктов</small>
            </span>
            <IconChevron size={16} className="modelcard__chev" />
          </button>

          {composer}

          <div className="cards cards--b">
            {PROMPTS_B.map((p, i) => (
              <button key={i} className="card" onClick={() => onSend(p.title)}>
                <span className="card__ic"><p.Icon size={18} /></span>
                <span className="card__title">{p.title}</span>
                <span className="card__sub">{p.sub}</span>
              </button>
            ))}
          </div>

          <button className="refresh"><IconRefresh size={15} /> Обновить подсказки</button>
        </div>
      </div>
    );
  }

  // Variant A — minimal, ChatGPT-like
  return (
    <div className="welcome welcome--a">
      <div className="welcome__inner">
        <Orb size={104} />
        <h1 className="welcome__title welcome__title--big">
          {greeting()}, Алексей<br />
          <span>С чего начнём сегодня?</span>
        </h1>

        {composer}

        <div className="welcome__hint">Попробуйте один из примеров</div>
        <div className="cards cards--a">
          {PROMPTS_A.map((p, i) => (
            <button key={i} className="card card--a" onClick={() => onSend(p.title)}>
              <span className="card__ic"><p.Icon size={18} /></span>
              <span className="card__body">
                <span className="card__title">{p.title}</span>
                <span className="card__sub">{p.sub}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

window.WelcomeScreen = WelcomeScreen;
