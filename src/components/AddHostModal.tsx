import {
  DialogBody,
  DialogButton,
  DialogFooter,
  DialogHeader,
  Focusable,
  ModalRoot,
  Spinner,
  TextField,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { backend } from "../backend";
import { notify } from "../lib/actions";
import { setState } from "../lib/store";
import type { DiscoveredHost } from "../types";
import { ButtonRow } from "./ButtonRow";
import { showPairModal } from "./PairModal";

/**
 * Add a PC: pick one found on the network, or type its address. The PC's
 * Sunshine login is optional; with it, Eclipse enters the pairing PIN there.
 */
function AddHostModal({ closeModal }: { closeModal?: () => void }) {
  const [found, setFound] = useState<DiscoveredHost[] | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const [tailscale, setTailscale] = useState<"running" | "stopped" | "missing" | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  /** A host already added, found at a new address (e.g. Tailscale): remember it to reach the host there. */
  const saveAddress = async (pc: DiscoveredHost) => {
    if (!pc.uuid) return;
    setSaving(pc.address);
    try {
      const res = await backend.addHostAddress(pc.uuid, pc.address);
      if (res.ok && res.settings) setState({ settings: res.settings });
      notify(
        res.ok
          ? `${pc.name} can now also be reached at ${pc.address}, e.g. away from home.`
          : (res.error ?? "Could not save the address"),
      );
      if (res.ok) closeModal?.();
    } catch (err) {
      notify(`Could not save the address: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(null);
    }
  };
  const [address, setAddress] = useState("");
  const [withLogin, setWithLogin] = useState(false);
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");

  const scan = useCallback(async () => {
    setFound(null);
    setScanError(null);
    try {
      const res = await backend.discoverHosts();
      setFound(res.items);
      setTailscale(res.tailscale);
      if (!res.scanned && res.items.length === 0) setScanError("This device is not connected to a network.");
    } catch (err) {
      setFound([]);
      setScanError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void scan();
  }, [scan]);

  const pair = (target: string) => {
    closeModal?.();
    const creds = withLogin && user && password ? { sunshineUser: user, sunshinePassword: password } : null;
    showPairModal({ address: target.trim(), host: null, creds });
  };

  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <DialogHeader>Add a host</DialogHeader>
      <DialogBody>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ opacity: 0.8 }}>
            A host is a PC running Sunshine (free, from app.lizardbyte.dev). Pick it below, or enter its address.
          </div>
          {found === null ? (
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0" }}>
              <Spinner style={{ width: 20, height: 20 }} />
              Looking for PCs on your network{tailscale === "running" ? " and Tailscale" : ""}...
            </div>
          ) : (
            <Focusable style={{ display: "flex", flexDirection: "column", gap: 6 }} flow-children="column">
              {found.map((pc) => (
                <DialogButton
                  key={pc.address}
                  disabled={(pc.known && !pc.newAddress) || saving !== null}
                  style={{ textAlign: "left" }}
                  onClick={() => (pc.known && pc.newAddress && pc.uuid ? void saveAddress(pc) : pair(pc.address))}
                >
                  {pc.name}
                  <span style={{ opacity: 0.6, marginLeft: 8 }}>
                    {pc.address}
                    {pc.via === "tailscale" ? " · via Tailscale" : ""}
                    {pc.known
                      ? pc.newAddress
                        ? saving === pc.address
                          ? " · saving..."
                          : " · added: press to also reach it at this address"
                        : " · already added"
                      : ""}
                  </span>
                </DialogButton>
              ))}
              {found.length === 0 && (
                <div style={{ opacity: 0.7 }}>
                  {scanError ?? "No PC found. Check that Sunshine runs on it and that it is on the same network."}
                </div>
              )}
              {tailscale === "stopped" && (
                <div style={{ opacity: 0.7 }}>
                  Tailscale is installed but not connected, so PCs away from home are not listed.
                </div>
              )}
              <DialogButton onClick={() => void scan()}>Search again</DialogButton>
            </Focusable>
          )}
          <TextField
            label="Or enter its address"
            description="IP address or name of the PC, e.g. 192.168.1.20"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
          <ToggleField
            label="Also enter the PC's Sunshine login"
            description="Optional: the login of Sunshine's web page on that PC. Eclipse then enters the pairing PIN there for you, and can switch its monitor and close games."
            checked={withLogin}
            onChange={setWithLogin}
          />
          {withLogin && (
            <>
              <TextField label="Sunshine username" value={user} onChange={(e) => setUser(e.target.value)} />
              <TextField
                label="Sunshine password"
                bIsPassword
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </>
          )}
        </Focusable>
      </DialogBody>
      <DialogFooter>
        <ButtonRow
          buttons={[
            { label: "Add this address", disabled: !address.trim(), onClick: () => pair(address) },
            { label: "Cancel", onClick: () => closeModal?.() },
          ]}
        />
      </DialogFooter>
    </ModalRoot>
  );
}

export function showAddHostModal(): void {
  showModal(<AddHostModal />);
}
