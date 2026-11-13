/** Small modal dialogs inside the window: confirm (No is the default), edit a text, or any form. Each returns a promise. */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { tr } from "../../shared/i18n";

type Open = { render: (close: (result: unknown) => void) => React.JSX.Element };
let current: Open | null = null;
const listeners = new Set<() => void>();

function show<T>(render: (close: (result: T) => void) => React.JSX.Element): Promise<T> {
  return new Promise((resolve) => {
    current = { render: (close) => render(close as (r: T) => void) };
    const done = (): void => listeners.forEach((l) => l());
    resolveCurrent = (r) => {
      current = null;
      done();
      resolve(r as T);
    };
    done();
  });
}
let resolveCurrent: (r: unknown) => void = () => undefined;

export const confirmBox = (title: string, text: string): Promise<boolean> => show<boolean>((close) => (
  <Frame title={title} onCancel={() => close(false)}>
    <p className="pre">{text}</p>
    <div className="buttons">
      <button className="primary" onClick={() => close(true)}>{tr("Yes", "Có")}</button>
      <button autoFocus onClick={() => close(false)}>{tr("No", "Không")}</button>
    </div>
  </Frame>
));

export const alertBox = (title: string, text: string): Promise<void> => show<void>((close) => (
  <Frame title={title} onCancel={() => close()}>
    <p className="pre">{text}</p>
    <div className="buttons"><button autoFocus onClick={() => close()}>OK</button></div>
  </Frame>
));

function TextForm({ label, initial, close }: { label: string; initial: string; close: (r: string | null) => void }): React.JSX.Element {
  const [text, setText] = useState(initial);
  return (
    <>
      <label>{label}</label>
      <textarea autoFocus rows={8} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="buttons">
        <button className="primary" onClick={() => close(text)}>OK</button>
        <button onClick={() => close(null)}>{tr("Cancel", "Hủy")}</button>
      </div>
    </>
  );
}

/** A multi-line text; null when cancelled. */
export const promptText = (title: string, label: string, initial: string): Promise<string | null> => show<string | null>((close) => (
  <Frame title={title} onCancel={() => close(null)}><TextForm label={label} initial={initial} close={close} /></Frame>
));

function LineForm({ label, initial, close }: { label: string; initial: string; close: (r: string | null) => void }): React.JSX.Element {
  const [text, setText] = useState(initial);
  return (
    <form onSubmit={(e) => { e.preventDefault(); close(text); }} style={{ display: "contents" }}>
      <label>{label}</label>
      <input type="text" autoFocus value={text} onChange={(e) => setText(e.target.value)} />
      <div className="buttons">
        <button type="submit" className="primary">OK</button>
        <button type="button" onClick={() => close(null)}>{tr("Cancel", "Hủy")}</button>
      </div>
    </form>
  );
}

/** One line of text; null when cancelled. */
export const promptLine = (title: string, label: string, initial: string): Promise<string | null> => show<string | null>((close) => (
  <Frame title={title} onCancel={() => close(null)}><LineForm label={label} initial={initial} close={close} /></Frame>
));

/** Any form: `body` gets `close(result)`; Escape closes with null. */
export const formBox = <T,>(title: string, body: (close: (r: T | null) => void) => React.JSX.Element): Promise<T | null> =>
  show<T | null>((close) => <Frame title={title} onCancel={() => close(null)}>{body(close)}</Frame>);

function Frame({ title, onCancel, children }: { title: string; onCancel: () => void; children: React.ReactNode }): React.JSX.Element {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  return (
    <div className="modal-back">
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={box}>
        <div className="title">{title}</div>
        {children}
      </div>
    </div>
  );
}

/** Mount once per window. */
export function DialogHost(): React.JSX.Element | null {
  useSyncExternalStore((l) => (listeners.add(l), () => listeners.delete(l)), () => current);
  return current ? current.render(resolveCurrent) : null;
}
