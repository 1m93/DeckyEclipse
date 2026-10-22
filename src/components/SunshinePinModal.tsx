import {
  DialogBody,
  DialogButton,
  DialogFooter,
  DialogHeader,
  Focusable,
  ModalRoot,
  Spinner,
  TextField,
  showModal,
} from "@decky/ui";
import { useEffect, useState } from "react";
import { FaCheckCircle, FaTimesCircle } from "react-icons/fa";
import { backend } from "../backend";
import type { PendingPairing } from "../types";
import { ButtonRow } from "./ButtonRow";

type Phase =
  | { kind: "loading" }
  | { kind: "choose"; items: PendingPairing[] }
  | { kind: "form" }
  | { kind: "pairing" }
  | { kind: "success"; client: string }
  | { kind: "failed"; error: string };

/**
 * Pair a Moonlight client with this device's Sunshine, showing each step and
 * the real outcome. With newer Sunshine the waiting devices are listed and
 * the result is final; older Sunshine is confirmed via the paired-client list.
 */
function SunshinePinModal({
  initial,
  onPaired,
  closeModal,
}: {
  initial?: PendingPairing;
  onPaired?: () => void;
  closeModal?: () => void;
}) {
  const [phase, setPhase] = useState<Phase>(initial ? { kind: "form" } : { kind: "loading" });
  const [target, setTarget] = useState<PendingPairing | null>(initial ?? null);
  const [supported, setSupported] = useState(Boolean(initial));
  const [pin, setPin] = useState("");
  const [name, setName] = useState(initial?.name ?? "");

  const submit = async () => {
    setPhase({ kind: "pairing" });
    const res = await backend.sunshineSubmitPin(pin, name, target?.id ?? null);
    if (res.ok && res.paired) {
      setPhase({ kind: "success", client: res.client ?? (name || "the device") });
      onPaired?.();
    } else if (res.choose?.length) {
      setPhase({ kind: "choose", items: res.choose });
    } else {
      setPhase({ kind: "failed", error: res.error ?? "Pairing failed" });
    }
  };

  let body;
  let buttons: { label: string; onClick: () => void; disabled?: boolean }[] = [
    { label: "Cancel", onClick: () => closeModal?.() },
  ];

  switch (phase.kind) {
    case "loading":
      body = (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Spinner style={{ width: 20, height: 20 }} />
          Looking for devices waiting to pair...
        </div>
      );
      break;
    case "choose":
      body = (
        <>
          <div style={{ marginBottom: 10 }}>Several devices are waiting. Which one shows the PIN?</div>
          <Focusable style={{ display: "flex", flexDirection: "column", gap: 6 }} flow-children="column">
            {phase.items.map((item) => (
              <DialogButton
                key={item.id}
                onClick={() => {
                  setTarget(item);
                  setName(item.name);
                  setPhase({ kind: "form" });
                }}
              >
                {item.name}
                {item.address ? ` (${item.address})` : ""}
              </DialogButton>
            ))}
          </Focusable>
        </>
      );
      break;
    case "form":
      body = (
        <>
          <div style={{ marginBottom: 10, opacity: 0.85 }}>
            {target
              ? `${target.name}${target.address ? ` (${target.address})` : ""} wants to pair. Enter the PIN it shows.`
              : supported
                ? "No device is waiting yet. In Moonlight on the other device, select this device to start pairing, then enter the PIN it shows."
                : "In Moonlight on the other device, select this device to start pairing, then enter the PIN it shows."}
          </div>
          <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <TextField
              label="PIN"
              mustBeNumeric
              value={pin}
              focusOnMount
              onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 4))}
            />
            <TextField label="Device name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
          </Focusable>
        </>
      );
      buttons = [
        { label: "Pair", disabled: pin.length !== 4, onClick: () => void submit() },
        ...(supported && !target ? [{ label: "Check again", onClick: () => setPhase({ kind: "loading" }) }] : []),
        ...buttons,
      ];
      break;
    case "pairing":
      body = (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Spinner style={{ width: 20, height: 20 }} />
          Pairing{target ? ` with ${target.name}` : ""}... Keep Moonlight open on the other device.
        </div>
      );
      buttons = [];
      break;
    case "success":
      body = (
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 18 }}>
          <FaCheckCircle color="#5ba32b" size={24} />
          Paired with {phase.client}
        </div>
      );
      buttons = [{ label: "Done", onClick: () => closeModal?.() }];
      break;
    case "failed":
      body = (
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <FaTimesCircle color="#ff6b6b" size={22} style={{ flexShrink: 0 }} />
          <span>{phase.error}</span>
        </div>
      );
      buttons = [
        {
          label: "Try again",
          onClick: () => {
            setPin("");
            setPhase(target ? { kind: "form" } : { kind: "loading" });
          },
        },
        ...buttons,
      ];
      break;
  }

  // Whenever the modal enters "loading" (on open and on "Check again"), find
  // out who is waiting to pair (newer Sunshine) before asking for the PIN.
  useEffect(() => {
    if (phase.kind !== "loading") return;
    let alive = true;
    void backend.sunshinePendingPairings().then((res) => {
      if (!alive) return;
      setSupported(res.supported);
      if (res.supported && res.items.length > 1) {
        setPhase({ kind: "choose", items: res.items });
        return;
      }
      if (res.items[0]) {
        setTarget(res.items[0]);
        setName(res.items[0].name);
      }
      setPhase({ kind: "form" });
    });
    return () => {
      alive = false;
    };
  }, [phase.kind]);

  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <DialogHeader>Pair a device with Sunshine</DialogHeader>
      <DialogBody>{body}</DialogBody>
      {buttons.length > 0 && (
        <DialogFooter>
          <ButtonRow buttons={buttons} />
        </DialogFooter>
      )}
    </ModalRoot>
  );
}

export function showSunshinePinModal(initial?: PendingPairing, onPaired?: () => void): void {
  showModal(<SunshinePinModal initial={initial} onPaired={onPaired} />);
}
