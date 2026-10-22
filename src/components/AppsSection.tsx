import {
  ButtonItem,
  ConfirmModal,
  DialogControlsSection,
  DialogControlsSectionHeader,
  Field,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { backend } from "../backend";
import { FEATURE_LABELS, installFeature, notify, uninstallApp, updateApp } from "../lib/actions";
import { useStore } from "../lib/store";
import type { AppStatus, AppsStatus, AppUpdateCheck, Feature } from "../types";
import { InstallProgress } from "./InstallProgress";

const FEATURES: Feature[] = ["moonlight", "sunshine"];

const DATA_DESCRIPTION: Record<Feature, string> = {
  moonlight: "Paired hosts and Moonlight settings. You will have to pair again after reinstalling.",
  sunshine: "Sunshine config, web UI credentials and paired clients.",
};

function UninstallModal({ feature, closeModal }: { feature: Feature; closeModal?: () => void }) {
  const [deleteData, setDeleteData] = useState(false);
  const label = FEATURE_LABELS[feature];
  return (
    <ConfirmModal
      strTitle={`Uninstall ${label}?`}
      strOKButtonText="Uninstall"
      bDestructiveWarning
      closeModal={closeModal}
      onCancel={closeModal}
      onOK={() => {
        closeModal?.();
        void uninstallApp(feature, deleteData);
      }}
    >
      <ToggleField
        label="Also delete app data"
        description={DATA_DESCRIPTION[feature]}
        checked={deleteData}
        onChange={setDeleteData}
      />
    </ConfirmModal>
  );
}

function describe(status: AppStatus): string {
  if (!status.installed) return "Not installed";
  if (!status.managed) return "Installed outside Flatpak (native or custom command). Manage it with the tool you installed it with.";
  const where = status.installation === "user" ? "Flatpak, this user" : `Flatpak, ${status.installation ?? "system"}`;
  return `${status.version ? `Version ${status.version}` : "Installed"} · ${where}`;
}

function AppRow({ feature, status, onChanged }: { feature: Feature; status: AppStatus; onChanged: () => void }) {
  const busy = useStore((s) => s.installing);
  const [check, setCheck] = useState<AppUpdateCheck | "checking" | null>(null);
  const label = FEATURE_LABELS[feature];

  if (busy === feature) {
    return (
      <>
        <Field label={label} bottomSeparator="none" />
        <InstallProgress />
      </>
    );
  }

  const runCheck = async () => {
    setCheck("checking");
    const res = await backend.checkAppUpdate(feature);
    setCheck(res);
    if (!res.ok) notify(res.error ?? "Update check failed");
  };

  const checkText =
    check === "checking"
      ? "Checking Flathub..."
      : check?.ok
        ? check.available
          ? `Update available${check.version ? `: ${check.version}` : ""}`
          : "Up to date"
        : undefined;

  return (
    <>
      <Field label={label} description={checkText ? `${describe(status)} · ${checkText}` : describe(status)} />
      {!status.installed && (
        <ButtonItem layout="below" disabled={busy !== null} onClick={async () => (await installFeature(feature)) && onChanged()}>
          Install {label}
        </ButtonItem>
      )}
      {status.managed && (
        <>
          {check !== "checking" && check?.ok && check.available ? (
            <ButtonItem
              layout="below"
              disabled={busy !== null}
              onClick={async () => {
                if (await updateApp(feature)) {
                  setCheck(null);
                  onChanged();
                }
              }}
            >
              Update {label}
              {check.version ? ` to ${check.version}` : ""}
            </ButtonItem>
          ) : (
            <ButtonItem layout="below" disabled={busy !== null || check === "checking"} onClick={() => void runCheck()}>
              Check for updates
            </ButtonItem>
          )}
          <ButtonItem
            layout="below"
            disabled={busy !== null}
            onClick={() => showModal(<UninstallModal feature={feature} />)}
          >
            Uninstall {label}
          </ButtonItem>
        </>
      )}
    </>
  );
}

/** Install, update-check, update and uninstall Moonlight and Sunshine. */
export function AppsSection() {
  const [status, setStatus] = useState<AppsStatus | null>(null);
  const busy = useStore((s) => s.installing);

  const refresh = useCallback(async () => setStatus(await backend.appsStatus()), []);

  // Re-read the install state whenever an operation finishes.
  useEffect(() => {
    if (!busy) void refresh();
  }, [busy, refresh]);

  return (
    <DialogControlsSection>
      <DialogControlsSectionHeader>Apps</DialogControlsSectionHeader>
      {!status ? (
        <Field description="Checking installed apps..." />
      ) : (
        FEATURES.map((feature) => (
          <AppRow key={feature} feature={feature} status={status[feature]} onChanged={() => void refresh()} />
        ))
      )}
    </DialogControlsSection>
  );
}
