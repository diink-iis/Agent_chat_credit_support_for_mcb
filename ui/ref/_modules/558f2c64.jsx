// chat.jsx — conversation thread + block renderer for AI messages

function AiBlocks({ blocks }) {
  return (
    <div className="ai__blocks">
      {blocks.map((b, i) => {
        if (b.type === 'p') return <p key={i} className="ai__p">{b.text}</p>;
        if (b.type === 'note') return (
          <div key={i} className="ai__note"><IconSparkle size={15} /><span>{b.text}</span></div>
        );
        if (b.type === 'list') return (
          <ul key={i} className="ai__list">
            {b.items.map((it, j) => (
              <li key={j} className={it.done ? 'is-done' : ''}>
                <span className="ai__check">{it.done ? <IconCheck size={13} /> : <i />}</span>
                {it.t}
              </li>
            ))}
          </ul>
        );
        if (b.type === 'stats') return (
          <div key={i} className="ai__stats">
            {b.items.map((s, j) => (
              <div key={j} className="ai__stat">
                <span className="ai__stat-k">{s.k}</span>
                <span className="ai__stat-v">{s.v}</span>
              </div>
            ))}
          </div>
        );
        if (b.type === 'score') return (
          <div key={i} className="ai__score">
            <div className="ai__score-ring" style={{ '--p': b.value }}>
              <span>{b.value}</span>
            </div>
            <div className="ai__score-txt">
              <strong>{b.label}</strong>
              <small>Кредитный рейтинг · из 100</small>
            </div>
          </div>
        );
        if (b.type === 'products') return (
          <div key={i} className="ai__products">
            {b.items.map((p, j) => (
              <div key={j} className={'prod' + (p.tag ? ' prod--rec' : '')}>
                {p.tag && <span className="prod__tag">{p.tag}</span>}
                <div className="prod__name">{p.name}</div>
                <div className="prod__rate">{p.rate}</div>
                <div className="prod__meta">
                  <span>{p.sum}</span><span className="prod__dot">·</span><span>{p.term}</span>
                </div>
                <div className="prod__note">{p.note}</div>
              </div>
            ))}
          </div>
        );
        return null;
      })}
    </div>
  );
}

function ChatScreen({ messages, typing, draft, setDraft, onSend, citation, setCitation }) {
  const endRef = React.useRef(null);
  React.useEffect(() => {
    const el = endRef.current;
    if (el) el.parentNode.scrollTop = el.parentNode.scrollHeight;
  }, [messages, typing]);

  return (
    <div className="chat">
      <div className="chat__scroll">
        <div className="chat__thread">
          {messages.map((m) => (
            m.role === 'user' ? (
              <div key={m.id} className="msg msg--user">
                <div className="msg__bubble">{m.text}</div>
              </div>
            ) : (
              <div key={m.id} className="msg msg--ai">
                <div className="msg__avatar"><Orb size={30} /></div>
                <div className="msg__ai">
                  <div className="msg__name">МСБ-Ассистент</div>
                  <AiBlocks blocks={m.blocks} />
                  <div className="msg__actions">
                    <button title="Копировать"><IconCopy size={15} /></button>
                    <button title="Сгенерировать заново"><IconRefresh size={15} /></button>
                  </div>
                </div>
              </div>
            )
          ))}
          {typing && (
            <div className="msg msg--ai">
              <div className="msg__avatar"><Orb size={30} /></div>
              <div className="msg__ai">
                <div className="msg__name">МСБ-Ассистент</div>
                <div className="typing"><i /><i /><i /></div>
              </div>
            </div>
          )}
          <div ref={endRef} className="chat__end" />
        </div>
      </div>

      <div className="chat__composer">
        <div className="chat__composer-inner">
          <Composer
            value={draft}
            onChange={setDraft}
            onSend={onSend}
            variant="docked"
            placeholder="Спросите про кредит, платёж или документы…"
            citation={citation}
            onToggleCitation={() => setCitation((c) => !c)}
          />
          <div className="chat__disclaimer">МСБ-Ассистент может ошибаться. Проверяйте важные условия перед подачей заявки.</div>
        </div>
      </div>
    </div>
  );
}

window.ChatScreen = ChatScreen;
