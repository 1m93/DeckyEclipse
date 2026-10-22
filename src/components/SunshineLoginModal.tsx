import { DialogBody, DialogFooter, DialogHeader, ModalRoot, showModal } from "@decky/ui";
import { ButtonRow } from "./ButtonRow";

function SunshineLoginModal({
  title,
  user,
  password,
  closeModal,
}: {
  title: string;
  user: string;
  password: string;
  closeModal?: () => void;
}) {
  const value = { fontFamily: "monospace", fontSize: 22, fontWeight: 700, letterSpacing: 1 } as const;
  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <DialogHeader>{title}</DialogHeader>
      <DialogBody>
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 16px", alignItems: "baseline" }}>
          <span style={{ opacity: 0.7 }}>Username</span>
          <span style={value}>{user}</span>
          <span style={{ opacity: 0.7 }}>Password</span>
          <span style={value}>{password}</span>
        </div>
        <div style={{ marginTop: 14, opacity: 0.8 }}>
          Eclipse saved this login and uses it on its own. You only need it to open Sunshine's web page from a
          browser. It can be shown again in Settings → Sharing.
        </div>
      </DialogBody>
      <DialogFooter>
        <ButtonRow buttons={[{ label: "Done", onClick: () => closeModal?.() }]} />
      </DialogFooter>
    </ModalRoot>
  );
}

export function showLocalSunshineLogin(title: string, user: string, password: string): void {
  showModal(<SunshineLoginModal title={title} user={user} password={password} />);
}
