import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Approvals } from "./Approvals";
import { Avatar } from "./Avatar";
import { MainWindow } from "./MainWindow";
import { Badge, Bubble, Highlight, Quick, Radial, Region, startSatellite } from "./Satellites";
import { store } from "./store";
import { Voice } from "./Voice";
import "./styles.css";

const role = location.hash.replace(/^#\//, "") || "main"; // one bundle, one page per window role
const SATELLITES: Record<string, () => React.JSX.Element | null> = { avatar: Avatar, bubble: Bubble, quick: Quick, radial: Radial, highlight: Highlight,
  badge: Badge, region: Region, voice: Voice };
const Page = SATELLITES[role] ?? (role === "approvals" ? Approvals : MainWindow);
if (role in SATELLITES) document.documentElement.classList.add("floating"); // see-through window: no page background

// The avatar and the voice engine drive devices by hand: mounting them twice (StrictMode) would double timers and microphones.
void (role in SATELLITES ? startSatellite() : store.start()).then(() => {
  createRoot(document.getElementById("root")!).render(role === "avatar" || role === "voice" ? <Page /> : <StrictMode><Page /></StrictMode>);
});
