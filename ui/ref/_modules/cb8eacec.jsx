// app.jsx — root: state, screen routing, top bar, tweaks

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "#0F9650",
  "font": "Inter",
  "radius": 12,
  "density": "regular",
  "variant": "A"
}/*EDITMODE-END*/;

const FONT_STACKS = {
  Inter: "'Inter', system-ui, sans-serif",
  Manrope: "'Manrope', system-ui, sans-serif",
  Jakarta: "'Plus Jakarta Sans', system-ui, sans-serif",
};

const ACCENTS = ['#0F9650', '#0B7A40', '#15A85A', '#0E8E6B'];

let __mid = 0;
const nextId = () => 'm' + (++__mid);

function TopBar({ onNewThread }) {
  return (
    <header className="topbar">
      <div className="topbar__left">
        <button className="model">
          <span className="model__dot" />
          <span className="model__name">МСБ&nbsp;GPT-5</span>
          <IconChevron size={15} />
        </button>
        <span className="topbar__crumb">AI-Ассистент</span>
      </div>
      <div className="topbar__right">
        <button className="topbar__search"><IconSearch size={16} /><span>Поиск по диалогам</span></button>
        <button className="topbar__new" onClick={onNewThread}><IconPlus size={16} />Новый диалог</button>
      </div>
    </header>
  );
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [active, setActive] = React.useState('assistant');
  const [view, setView] = React.useState('welcome'); // welcome | chat
  const [messages, setMessages] = React.useState([]);
  const [typing, setTyping] = React.useState(false);
  const [draft, setDraft] = React.useState('');
  const [citation, setCitation] = React.useState(true);
  const timerRef = React.useRef(null);

  const handleSend = React.useCallback((text) => {
    const userMsg = { id: nextId(), role: 'user', text };
    setMessages((prev) => [...prev, userMsg]);
    setDraft('');
    setView('chat');
    setActive('assistant');
    setTyping(true);
    const res = generateResponse(text);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setTyping(false);
      setMessages((prev) => [...prev, { id: nextId(), role: 'ai', blocks: res.blocks }]);
    }, res.delay || 1200);
  }, []);

  const newThread = React.useCallback(() => {
    clearTimeout(timerRef.current);
    setMessages([]);
    setTyping(false);
    setDraft('');
    setView('welcome');
    setActive('assistant');
  }, []);

  const rootStyle = {
    '--accent': t.accent,
    '--radius': t.radius + 'px',
    fontFamily: FONT_STACKS[t.font] || FONT_STACKS.Inter,
  };

  return (
    <div className="app" data-density={t.density} style={rootStyle}>
      <Sidebar active={active} onNavigate={setActive} />
      <main className="main">
        <TopBar onNewThread={newThread} />
        <div className="main__body">
          {view === 'welcome' ? (
            <WelcomeScreen
              variant={t.variant}
              draft={draft}
              setDraft={setDraft}
              onSend={handleSend}
              citation={citation}
              setCitation={setCitation}
            />
          ) : (
            <ChatScreen
              messages={messages}
              typing={typing}
              draft={draft}
              setDraft={setDraft}
              onSend={handleSend}
              citation={citation}
              setCitation={setCitation}
            />
          )}
        </div>
      </main>

      <TweaksPanel>
        <TweakSection label="Внешний вид" />
        <TweakColor label="Акцент" value={t.accent} options={ACCENTS}
          onChange={(v) => setTweak('accent', v)} />
        <TweakRadio label="Шрифт" value={t.font}
          options={['Inter', 'Manrope', 'Jakarta']}
          onChange={(v) => setTweak('font', v)} />
        <TweakSlider label="Скругление" value={t.radius} min={4} max={22} step={1} unit="px"
          onChange={(v) => setTweak('radius', v)} />
        <TweakRadio label="Плотность" value={t.density}
          options={[{ value: 'compact', label: 'Плотно' }, { value: 'regular', label: 'Обычно' }, { value: 'comfy', label: 'Просторно' }]}
          onChange={(v) => setTweak('density', v)} />
        <TweakSection label="Главный экран" />
        <TweakRadio label="Макет" value={t.variant}
          options={[{ value: 'A', label: 'Вариант A' }, { value: 'B', label: 'Вариант B' }]}
          onChange={(v) => setTweak('variant', v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
