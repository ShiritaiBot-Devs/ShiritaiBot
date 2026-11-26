import { LoadingBar } from "./components/LoadingBar";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { applyInstanceLocale } from "./i18n";
import { setOwnerTimeZone } from "./lib/utils";
import { ApiError, createClient } from "./api/client";
import type { ApiClient } from "./api/client";
import type { HistoryMessage } from "./api/types";
import { AppShell } from "./components/AppShell";
import { PageTabs } from "./components/PageTabs";
import { CAPABILITY_TABS, type PageId } from "./navigation";
import { FirstRunChecklist } from "./components/FirstRunChecklist";
import { ChatWorkspace } from "./components/ChatWorkspace";
import { LoginView } from "./components/LoginView";
import { desktop, DesktopTitleBar } from "./components/DesktopTitleBar";
import { SaveBar } from "./components/SaveBar";
import { UpdateDialog } from "./components/UpdateDialog";
import { isNewer } from "./lib/version";
import { useConfigDraft } from "./hooks/useConfigDraft";
import { useHashRoute } from "./hooks/useHashRoute";
import { NoticeProvider, useNotice } from "./hooks/useNotice";
import { useRealtime } from "./hooks/useRealtime";
import type { RealtimeEvent } from "./hooks/useRealtime";
import { AdaptersPage } from "./pages/AdaptersPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { ChatPage } from "./pages/ChatPage";
import { EventsPage } from "./pages/EventsPage";
import { LogsPage } from "./pages/LogsPage";
import { McpPage } from "./pages/McpPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { MemesPage } from "./pages/MemesPage";
import { MemoryPage } from "./pages/MemoryPage";
import { ModelsPage } from "./pages/ModelsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PluginsPage } from "./pages/PluginsPage";
import { RoutinePage } from "./pages/RoutinePage";
import { SettingsPage } from "./pages/SettingsPage";
import { SkillsPage } from "./pages/SkillsPage";
import { SubagentsPage } from "./pages/SubagentsPage";
import type { Action } from "./pages/common";

const TOKEN_KEY = "shiritaibot-token";
const MODE_KEY = "shiritaibot-mode";

export function App() {
  return (
    <NoticeProvider>
      <DesktopTitleBar />
      <ConsoleApp />
    </NoticeProvider>
  );
}

function ConsoleApp() {
  const [linkToken, setLinkToken] = useState(() =>
    location.pathname === "/token"
      ? new URLSearchParams(location.hash.slice(1)).get("token")
      : null,
  );
  const [token, setToken] = useState<string | null>(() =>
    linkToken ? null : sessionStorage.getItem(TOKEN_KEY),
  );
  const [page, navigate] = useHashRoute();
  const capability = CAPABILITY_TABS.find((tab) => tab.id === page);
  const { succeed, fail, warn, dismiss } = useNotice();
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartInstance, setRestartInstance] = useState<string | null>(null);
  const [updateOpen, setUpdateOpen] = useState(false);
  const [mode, setMode] = useState<"console" | "chat">(() =>
    desktop || localStorage.getItem(MODE_KEY) === "chat" ? "chat" : "console",
  );
  const [session, setSession] = useState(
    () => sessionStorage.getItem("shiritaibot-chat-session") || "web:owner",
  );
  const [drafts, setDrafts] = useState<string[]>([]);
  const queryClient = useQueryClient();

  useEffect(() => {
    localStorage.setItem(MODE_KEY, mode);
  }, [mode]);

  useEffect(() => {
    sessionStorage.setItem("shiritaibot-chat-session", session);
  }, [session]);

  const startConversation = useCallback((id: string) => {
    setDrafts((previous) => [...previous, id]);
    setSession(id);
  }, []);

  const authenticate = useCallback(
    (next: string) => {
      dismiss();
      sessionStorage.setItem(TOKEN_KEY, next);
      setToken(next);
    },
    [dismiss],
  );

  useLayoutEffect(() => {
    if (location.pathname === "/token") {
      history.replaceState(history.state, "", "/");
      if (linkToken) sessionStorage.removeItem(TOKEN_KEY);
    }
  }, [linkToken]);

  useEffect(() => {
    if (!linkToken) return;
    let active = true;
    void createClient(() => linkToken)
      .health()
      .then(() => {
        if (active) authenticate(linkToken);
      })
      .catch((error: unknown) => {
        if (active)
          fail(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (active) setLinkToken(null);
      });
    return () => {
      active = false;
    };
  }, [linkToken, authenticate, fail]);

  const client: ApiClient | null = useMemo(
    () => (token ? createClient(() => token) : null),
    [token],
  );
  const draft = useConfigDraft(client);
  // Re-render the console when the interface language changes.
  const { t } = useTranslation(["shell", "common"]);
  const savedSettings = draft.saved?.settings;
  // Idempotent module setting read by every date formatter, so it must be set
  // before this render's children format anything.
  setOwnerTimeZone(savedSettings?.proactive.timezone);
  useEffect(() => {
    applyInstanceLocale(savedSettings?.locale);
  }, [savedSettings?.locale]);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => client!.health(),
    enabled: client !== null,
    refetchInterval: restarting ? 1000 : 10000,
    retry: restarting ? false : 3,
  });
  // Background check follows the deployment mode; failures stay silent here.
  const updateCheck = useQuery({
    queryKey: ["update", "check", health.data?.instance_id],
    queryFn: async () => {
      const status = await client!.updateStatus();
      if (status.mode === "git") {
        if (!status.available) return false;
        const { commit } = await client!.updatePreview();
        return Boolean(
          commit && status.current.sha && commit.sha !== status.current.sha,
        );
      }
      const latest = await client!.updateReleases(1, 1, false);
      const release = latest?.items?.[0];
      return Boolean(
        release && status?.current && isNewer(release, status.current),
      );
    },
    enabled: !desktop && client !== null && health.isSuccess && !restarting,
    staleTime: 30 * 60 * 1000,
    refetchInterval: 6 * 60 * 60 * 1000,
    retry: false,
  });
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client!.records("approval"),
    enabled: client !== null,
    refetchInterval: 4000,
  });

  // Surface transport/auth failures instead of silently showing empty pages.
  const queryError =
    draft.error ??
    (health.error instanceof Error ? health.error : null) ??
    (client && health.isError ? new Error(t("common:errors.offline")) : null);
  useEffect(() => {
    if (!queryError || restarting) return;
    // By status, not message text: the message follows the interface language.
    if (queryError instanceof ApiError && queryError.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      setToken(null);
    }
    fail(queryError.message);
  }, [queryError, restarting, fail]);

  useEffect(() => {
    if (
      !restartInstance ||
      !health.data?.instance_id ||
      health.data.instance_id === restartInstance
    )
      return;
    setRestarting(false);
    setRestartInstance(null);
    succeed(t("restart.done"));
    void queryClient.invalidateQueries();
  }, [restartInstance, health.data?.instance_id, queryClient, succeed, t]);

  useEffect(() => {
    if (!restarting) return;
    const timer = setTimeout(() => {
      setRestarting(false);
      setRestartInstance(null);
      fail(t("restart.timeout"));
    }, 60000);
    return () => clearTimeout(timer);
  }, [restarting, fail, t]);

  const onRealtime = useCallback(
    (event: RealtimeEvent) => {
      if (event.kind === "task_plan" && event.session) {
        void queryClient.invalidateQueries({ queryKey: ["task-plan", event.session] });
        return;
      }
      if (event.kind === "chat_history") {
        if (desktop && event.session)
          void queryClient.invalidateQueries({
            queryKey: ["history", event.session],
          });
        return;
      }
      if (event.kind === "chat_reply" || event.kind === "chat_reply_end") {
        if (!desktop || !event.session || !event.id) return;
        const key = ["chat-reply", event.session];
        if (event.kind === "chat_reply" && typeof event.text === "string") {
          queryClient.setQueryData(key, {
            role: "assistant",
            content: event.text,
            _stream_id: event.id,
          });
        } else if (event.kind === "chat_reply_end") {
          const clear = () =>
            queryClient.setQueryData<HistoryMessage | null>(key, (preview) =>
              preview?._stream_id === event.id ? null : preview,
            );
          if (event.committed) {
            void queryClient
              .invalidateQueries({ queryKey: ["history", event.session] })
              .then(clear);
          } else {
            clear();
          }
        }
        return;
      }
      void queryClient.invalidateQueries({ queryKey: ["health"] });
      void queryClient.invalidateQueries({ queryKey: ["records"] });
      if (event.kind === "web_context") {
        void queryClient.invalidateQueries({
          queryKey: ["chat-context", event.id],
        });
      }
      if (event.kind === "turn" || event.kind === "reply") {
        void queryClient.invalidateQueries({ queryKey: ["history"] });
      }
    },
    [queryClient],
  );
  const onRealtimeDisconnect = useCallback(() => {
    queryClient.setQueriesData({ queryKey: ["chat-reply"] }, null);
  }, [queryClient]);
  useRealtime(token, onRealtime, onRealtimeDisconnect);

  const pending = (approvals.data ?? []).filter(
    (row) => row.status === "pending" && row.reviewer !== "model",
  ).length;

  const act: Action = useCallback(
    async (work, successText) => {
      setBusy(true);
      try {
        const result = await work();
        if (successText !== "") {
          succeed(
            successText ??
              (result &&
              typeof result === "object" &&
              "text" in result &&
              typeof (result as { text?: unknown }).text === "string"
                ? String((result as { text: string }).text).slice(0, 500)
                : t("common:done")),
          );
        }
        await queryClient.invalidateQueries();
      } catch (error) {
        fail(error instanceof Error ? error.message : String(error));
      } finally {
        setBusy(false);
      }
    },
    [queryClient, succeed, fail, t],
  );

  async function save() {
    setSaving(true);
    try {
      const result = await draft.save();
      const warnings = result?.warnings ?? [];
      if (warnings.length) {
        warn(
          t("config.savedWithWarnings", {
            warnings: warnings.join(t("common:listSeparator")),
          }),
        );
      } else {
        succeed(t("config.saved"));
      }
      await queryClient.invalidateQueries({ queryKey: ["health"] });
    } catch (error) {
      fail(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  async function restart() {
    if (!client || restarting || busy || saving || draft.dirty) return;
    dismiss();
    setRestarting(true);
    try {
      const result = await client.restart();
      setRestartInstance(result.instance_id);
    } catch (error) {
      setRestarting(false);
      fail(error instanceof Error ? error.message : String(error));
    }
  }

  const startUpdateRestart = useCallback(() => {
    dismiss();
    setRestarting(true);
    setRestartInstance(health.data?.instance_id ?? null);
  }, [dismiss, health.data?.instance_id]);

  if (!client) {
    return (
      <LoginView
        authenticating={Boolean(linkToken)}
        onAuthenticated={authenticate}
        onError={fail}
      />
    );
  }

  if (mode === "chat") {
    function openConsole(page: PageId) {
      setMode("console");
      navigate(page);
    }
    return (
      <ChatWorkspace
        client={client}
        act={act}
        assistantName={draft.config?.assistant_name ?? "ShiritaiBot"}
        session={session}
        onSelectSession={setSession}
        onCreateSession={startConversation}
        onForgetSession={(id) =>
          setDrafts((previous) => previous.filter((item) => item !== id))
        }
        draftSessions={drafts}
        onOpenSettings={() => openConsole("settings")}
        setup={
          desktop && savedSettings && session.startsWith("web:") ? (
            <FirstRunChecklist
              config={savedSettings}
              onNavigate={openConsole}
              compact
            />
          ) : undefined
        }
      />
    );
  }

  function content(api: ApiClient) {
    if (!draft.config) {
      return draft.loading ? <LoadingBar label={t("config.loading")} /> : null;
    }
    const configProps = {
      client: api,
      act,
      config: draft.config,
      setConfig: draft.update,
    };
    switch (page) {
      case "overview":
        return (
          <OverviewPage
            client={api}
            act={act}
            onNavigate={navigate}
            setupConfig={desktop ? undefined : savedSettings}
          />
        );
      case "chat":
        return (
          <ChatPage
            client={api}
            act={act}
            assistantName={draft.config.assistant_name}
            session={session}
            onSelectSession={setSession}
          />
        );
      case "events":
        return <EventsPage client={api} act={act} />;
      case "routine":
        return <RoutinePage client={api} act={act} />;
      case "memory":
        return (
          <MemoryPage
            {...configProps}
            currentSession={session}
            draftSessions={drafts}
          />
        );
      case "memes":
        return <MemesPage {...configProps} />;
      case "knowledge":
        return (
          <KnowledgePage
            {...configProps}
            currentSession={session}
            draftSessions={drafts}
          />
        );
      case "logs":
        return <LogsPage client={api} act={act} />;
      case "models":
        return <ModelsPage {...configProps} initialSession={session} />;
      case "adapters":
        return <AdaptersPage {...configProps} dirty={draft.dirty} />;
      case "subagents":
        return <SubagentsPage {...configProps} />;
      case "plugins":
        return <PluginsPage {...configProps} />;
      case "skills":
        return <SkillsPage {...configProps} />;
      case "mcp":
        return <McpPage {...configProps} />;
      case "approvals":
        return (
          <ApprovalsPage
            {...configProps}
            saved={draft.saved}
            initialSession={session}
          />
        );
      case "settings":
        return <SettingsPage {...configProps} />;
      default:
        return null;
    }
  }

  return (
    <AppShell
      page={page}
      onNavigate={navigate}
      pendingApprovals={pending}
      online={health.isSuccess && !restarting}
      version={health.data?.version ? "v" + health.data.version : ""}
      onOpenChat={() => setMode("chat")}
      onRestart={() => void restart()}
      restarting={restarting}
      restartDisabled={
        busy ||
        saving ||
        draft.dirty ||
        !health.isSuccess ||
        health.data?.restart_available === false
      }
      restartHint={
        draft.dirty
          ? t("config.unsaved")
          : health.data?.restart_available === false
            ? t("restart.unsupported")
            : t("restart.hint")
      }
      onCheckUpdate={
        desktop ? () => desktop?.action("releases") : () => setUpdateOpen(true)
      }
      updateAvailable={updateCheck.data === true}
      onLogout={() => {
        dismiss();
        setUpdateOpen(false);
        setRestarting(false);
        setRestartInstance(null);
        sessionStorage.removeItem(TOKEN_KEY);
        setToken(null);
        queryClient.clear();
      }}
    >
      {busy && <LoadingBar label={t("busy.working")} />}
      {capability ? (
        <>
          <PageTabs
            tabs={CAPABILITY_TABS.map(({ id }) => ({
              id,
              label: t(`capabilities.${id}`),
            }))}
            active={capability.id}
            onSelect={navigate}
            prefix="capability"
            label={t("pages.approvals.label")}
          />
          {CAPABILITY_TABS.map(({ id }) => (
            <div
              key={id}
              role="tabpanel"
              id={`capability-panel-${id}`}
              aria-labelledby={`capability-tab-${id}`}
              hidden={page !== id}
            >
              {page === id && content(client)}
            </div>
          ))}
        </>
      ) : (
        content(client)
      )}
      {updateOpen && (
        <UpdateDialog
          client={client}
          onClose={() => setUpdateOpen(false)}
          onRestarting={startUpdateRestart}
          restarting={restarting}
          blockedReason={
            draft.dirty
              ? t("config.unsaved")
              : busy || saving
                ? t("busy.wait")
                : null
          }
        />
      )}
      {draft.dirty && (
        <SaveBar
          busy={saving}
          onSave={() => void save()}
          onDiscard={draft.discard}
        />
      )}
    </AppShell>
  );
}
