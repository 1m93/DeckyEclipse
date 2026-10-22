import { ConfirmModal, Spinner, showModal } from "@decky/ui";
import { useEffect, useState } from "react";
import { launchPairing, preparePairing } from "../lib/actions";
import type { Host, HostConfig } from "../types";

interface Props {
  /** Address to pair with; empty means "use the host's best known address". */
  address: string;
  host: Host | null;
  creds?: Partial<HostConfig> | null;
  closeModal?: () => void;
}

function PairModal({ address, host, creds = null, closeModal }: Props) {
  const [pairing, setPairing] = useState<{ pin: string; autoSubmit: boolean } | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    void preparePairing(address, host, creds).then((res) => {
      if (!alive) return;
      if (res) setPairing(res);
      else setFailed(true);
    });
    return () => {
      alive = false;
    };
  }, [address, host, creds]);

  const target = host?.name ?? address;

  return (
    <ConfirmModal
      strTitle={`Pair with ${target}`}
      strOKButtonText="Start pairing"
      bOKDisabled={!pairing}
      closeModal={closeModal}
      onCancel={closeModal}
      onOK={() => {
        if (pairing) void launchPairing(pairing.pin, address || host?.addresses[0]?.address || host?.name || "");
        closeModal?.();
      }}
    >
      {failed ? (
        <div>Pairing could not be prepared. Check that Moonlight is installed.</div>
      ) : !pairing ? (
        <Spinner style={{ width: 32, height: 32 }} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
          <div style={{ fontSize: 56, fontWeight: 700, letterSpacing: 16, fontFamily: "monospace" }}>{pairing.pin}</div>
          {pairing.autoSubmit ? (
            <div style={{ textAlign: "center" }}>
              Eclipse will send this PIN to Sunshine automatically once Moonlight starts pairing.
            </div>
          ) : (
            <div style={{ textAlign: "center" }}>
              On the host, open the Sunshine web UI (https://&lt;host&gt;:47990), go to <b>PIN</b> and enter this
              code. Moonlight will open and wait for it. The PIN also stays visible as a notification.
            </div>
          )}
        </div>
      )}
    </ConfirmModal>
  );
}

export function showPairModal(props: Omit<Props, "closeModal">): void {
  showModal(<PairModal {...props} />);
}
