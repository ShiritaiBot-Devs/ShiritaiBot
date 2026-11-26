import { Download, TriangleAlert, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { PageProps } from "../pages/common";

export function ConfigTransfer({
  client,
  act,
  dirty,
  revision,
}: PageProps & { dirty: boolean; revision?: string }) {
  const { t } = useTranslation(["settings", "shell", "common"]);
  const input = useRef<HTMLInputElement>(null);
  const [working, setWorking] = useState(false);
  const [selected, setSelected] = useState<{
    file: File;
    revision?: string;
  } | null>(null);

  async function download() {
    setWorking(true);
    try {
      await act(async () => {
        const url = URL.createObjectURL(await client.exportConfig());
        const link = document.createElement("a");
        link.href = url;
        link.download = "shiritaibot-config.toml";
        document.body.append(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }, "");
    } finally {
      setWorking(false);
    }
  }

  async function importSelected() {
    if (!selected || dirty) return;
    setSelected(null);
    setWorking(true);
    try {
      await act(async () => {
        const result = await client.importConfig(
          selected.file,
          selected.revision,
        );
        const warnings = result.warnings ?? [];
        return {
          text: warnings.length
            ? t("shell:config.savedWithWarnings", {
                warnings: warnings.join(t("common:listSeparator")),
              })
            : t("settings:transfer.imported"),
        };
      });
    } finally {
      setWorking(false);
    }
  }

  return (
    <section className="settings-group">
      <h2>{t("settings:transfer.title")}</h2>
      <p className="subscription-warning" role="note">
        <TriangleAlert size={16} aria-hidden="true" />
        {t("settings:transfer.sensitive")}
      </p>
      <div className="inline">
        <button
          type="button"
          disabled={working}
          onClick={() => void download()}
        >
          <Download size={16} />
          {t("settings:transfer.export")}
        </button>
        <button
          type="button"
          disabled={working || dirty}
          onClick={() => input.current?.click()}
        >
          <Upload size={16} />
          {t("settings:transfer.import")}
        </button>
        <input
          ref={input}
          type="file"
          accept=".toml"
          hidden
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = "";
            if (file) setSelected({ file, revision });
          }}
        />
      </div>
      {dirty && <small className="muted">{t("settings:transfer.dirty")}</small>}
      {selected && (
        <div>
          <p>{t("settings:transfer.confirm", { name: selected.file.name })}</p>
          <div className="inline">
            <button
              type="button"
              disabled={working || dirty}
              onClick={() => void importSelected()}
            >
              {t("settings:transfer.replace")}
            </button>
            <button type="button" onClick={() => setSelected(null)}>
              {t("common:cancel")}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
