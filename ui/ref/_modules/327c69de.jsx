// sidebar.jsx — left navigation rail for МСБ-Ассистент
const NAV = [
  { id: 'dashboard', label: 'Дашборд', Icon: IconDashboard },
  { id: 'applications', label: 'Заявки', Icon: IconApplications, badge: '3' },
  { id: 'products', label: 'Кредитные продукты', Icon: IconProducts },
  { id: 'calculator', label: 'Калькулятор', Icon: IconCalculator },
  { id: 'scoring', label: 'Скоринг', Icon: IconScoring },
  { id: 'assistant', label: 'AI-Ассистент', Icon: IconSparkle },
  { id: 'documents', label: 'Документы', Icon: IconDocs },
  { id: 'reports', label: 'Отчёты', Icon: IconReports },
  { id: 'clients', label: 'Клиенты', Icon: IconClients },
];

function Sidebar({ active, onNavigate, collapsed }) {
  return (
    <aside className={'sb' + (collapsed ? ' sb--collapsed' : '')}>
      <div className="sb__brand">
        <div className="sb__logo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M5 16.5 12 5l7 11.5" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M8.5 16.5 12 10.5l3.5 6" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" opacity=".55" />
          </svg>
        </div>
        <div className="sb__brandtext">
          <span className="sb__brandname">МСБ-Ассистент</span>
          <span className="sb__brandsub">Кредитование бизнеса</span>
        </div>
        <IconChevron size={16} className="sb__brandchev" />
      </div>

      <div className="sb__search">
        <IconSearch size={16} />
        <input placeholder="Поиск…" aria-label="Поиск" />
        <kbd>/</kbd>
      </div>

      <nav className="sb__nav">
        {NAV.map(({ id, label, Icon, badge }) => (
          <button
            key={id}
            className={'sb__item' + (active === id ? ' is-active' : '')}
            onClick={() => onNavigate(id)}
          >
            <Icon size={18} />
            <span className="sb__label">{label}</span>
            {badge && <span className="sb__badge">{badge}</span>}
          </button>
        ))}
      </nav>

      <div className="sb__spacer" />

      <div className="sb__promo">
        <div className="sb__promo-icon"><IconSparkle size={18} /></div>
        <div className="sb__promo-title">Тариф Pro · пробный</div>
        <div className="sb__promo-text">Осталось 12 дней расширенного доступа к AI-аналитике.</div>
        <button className="sb__promo-btn">Перейти на Pro</button>
      </div>

      <div className="sb__foot">
        <button className="sb__footitem" onClick={() => onNavigate('settings')}>
          <IconSettings size={18} /><span>Настройки</span>
        </button>
        <button className="sb__footitem" onClick={() => onNavigate('help')}>
          <IconHelp size={18} /><span>Помощь</span>
        </button>
        <div className="sb__user">
          <div className="sb__avatar">АК</div>
          <div className="sb__userinfo">
            <span className="sb__username">Алексей Климов</span>
            <span className="sb__userrole">ООО «Северторг»</span>
          </div>
          <IconLogout size={16} className="sb__logout" />
        </div>
      </div>
    </aside>
  );
}

window.Sidebar = Sidebar;
