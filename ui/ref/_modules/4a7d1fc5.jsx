// icons.jsx — simple line icons for МСБ-Ассистент UI chrome
const Ic = ({ d, size = 20, sw = 1.6, fill = false, children }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth={sw} strokeLinecap="round"
       strokeLinejoin="round" aria-hidden="true">
    {d ? <path d={d} /> : children}
  </svg>
);

const IconDashboard = (p) => (
  <Ic {...p}><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></Ic>
);
const IconApplications = (p) => (
  <Ic {...p}><path d="M14 3v4a1 1 0 0 0 1 1h4" /><path d="M19 8v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7z" /><path d="M9 13h6M9 17h4" /></Ic>
);
const IconProducts = (p) => (
  <Ic {...p}><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M3 10h18" /><path d="M7 15h3" /></Ic>
);
const IconCalculator = (p) => (
  <Ic {...p}><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M8 7h8" /><path d="M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15v2M8 18h4" /></Ic>
);
const IconScoring = (p) => (
  <Ic {...p}><path d="M3 3v18h18" /><path d="M7 15l3-4 3 2 5-7" /></Ic>
);
const IconSparkle = (p) => (
  <Ic {...p}><path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3z" /><path d="M18 14l.7 1.9L20.5 17l-1.8.6L18 19.5l-.7-1.9L15.5 17l1.8-.6L18 14z" /></Ic>
);
const IconDocs = (p) => (
  <Ic {...p}><path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7z" /></Ic>
);
const IconReports = (p) => (
  <Ic {...p}><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></Ic>
);
const IconClients = (p) => (
  <Ic {...p}><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0" /><path d="M16 6.2a3 3 0 0 1 0 5.6" /><path d="M17.5 19a5.5 5.5 0 0 0-2-4.2" /></Ic>
);
const IconSearch = (p) => (
  <Ic {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.2-3.2" /></Ic>
);
const IconSettings = (p) => (
  <Ic {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 8.3 19a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H2a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 5 8.3a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V2a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H22a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" /></Ic>
);
const IconHelp = (p) => (
  <Ic {...p}><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 0 1 4.6 1.3c0 1.7-2.6 2-2.6 2" /><path d="M12 16.5h.01" /></Ic>
);
const IconLogout = (p) => (
  <Ic {...p}><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" /><path d="M10 17l-5-5 5-5" /><path d="M5 12h11" /></Ic>
);
const IconAttach = (p) => (
  <Ic {...p}><path d="M21 11.5l-8.5 8.5a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8" /></Ic>
);
const IconSend = (p) => (
  <Ic {...p}><path d="M5 12h13" /><path d="M13 6l6 6-6 6" /></Ic>
);
const IconPlus = (p) => (
  <Ic {...p}><path d="M12 5v14M5 12h14" /></Ic>
);
const IconChevron = (p) => (
  <Ic {...p}><path d="m6 9 6 6 6-6" /></Ic>
);
const IconImage = (p) => (
  <Ic {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8.5" cy="9.5" r="1.5" /><path d="m4 17 5-5 4 4 3-3 4 4" /></Ic>
);
const IconMic = (p) => (
  <Ic {...p}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></Ic>
);
const IconSidebar = (p) => (
  <Ic {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></Ic>
);
const IconCopy = (p) => (
  <Ic {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></Ic>
);
const IconRefresh = (p) => (
  <Ic {...p}><path d="M3 12a9 9 0 0 1 15-6.7L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" /><path d="M3 21v-5h5" /></Ic>
);
const IconCheck = (p) => (
  <Ic {...p}><path d="M5 12.5 10 17l9-10" /></Ic>
);

Object.assign(window, {
  IconDashboard, IconApplications, IconProducts, IconCalculator, IconScoring,
  IconSparkle, IconDocs, IconReports, IconClients, IconSearch, IconSettings,
  IconHelp, IconLogout, IconAttach, IconSend, IconPlus, IconChevron, IconImage,
  IconMic, IconSidebar, IconCopy, IconRefresh, IconCheck,
});
