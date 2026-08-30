"use client";

import { useCallback, useEffect, useState } from "react";

import { DegradationBanner } from "@/components/receptionist/DegradationBanner";
import { StatusPill } from "@/components/receptionist/StatusPill";
import { TokenGate } from "@/components/receptionist/TokenGate";
import { Button } from "@/components/ui/Button";
import { Section } from "@/components/ui/Section";
import { nightsBetween, shortDate, shortDateTime } from "@/lib/format";
import {
  CONVERSATION_STATUSES,
  INQUIRY_STATUSES,
  StaffApiError,
  getDegradation,
  getKnowledgeHealth,
  getMetrics,
  listConversations,
  listInquiries,
  setInquiryStatus,
  type BookingInquiry,
  type ConversationSummary,
  type Degradation,
  type InquiryStatus,
  type KnowledgeHealth,
  type Metrics,
} from "@/lib/staff";

const HOTEL_ID = Number(process.env.NEXT_PUBLIC_WIDGET_HOTEL_ID || "1");

// Polling rather than WebSocket, per the slice decision. Paused while the
// tab is hidden so a dashboard left open overnight is not hammering the API.
const POLL_MS = 5000;

export default function StaffDashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [inquiries, setInquiries] = useState<BookingInquiry[]>([]);
  const [health, setHealth] = useState<KnowledgeHealth | null>(null);
  const [degradation, setDegradation] = useState<Degradation | null>(null);
  const [convoFilter, setConvoFilter] = useState<string>("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [m, c, i, h, d] = await Promise.all([
        getMetrics(HOTEL_ID),
        listConversations(HOTEL_ID, convoFilter || undefined),
        listInquiries(HOTEL_ID),
        getKnowledgeHealth(HOTEL_ID),
        getDegradation(HOTEL_ID),
      ]);
      setMetrics(m);
      setConversations(c);
      setInquiries(i);
      setHealth(h);
      setDegradation(d);
      setAuthError(null);
      setError(null);
    } catch (err) {
      if (err instanceof StaffApiError && err.isAuthProblem) {
        setAuthError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "Could not load data.");
      }
    } finally {
      setLoaded(true);
    }
  }, [convoFilter]);

  useEffect(() => {
    refresh();
    let timer: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (timer === null) timer = setInterval(refresh, POLL_MS);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        refresh();
        start();
      }
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  async function changeInquiry(id: number, status: InquiryStatus) {
    // Optimistic: the dropdown should not feel laggy. A failure refetches
    // and the real value snaps back.
    setInquiries((prev) =>
      prev.map((q) => (q.id === id ? { ...q, status } : q))
    );
    try {
      await setInquiryStatus(HOTEL_ID, id, status);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update.");
    } finally {
      refresh();
    }
  }

  if (authError) {
    return <TokenGate message={authError} onSaved={refresh} />;
  }
  if (!loaded) return <p>Loading dashboard…</p>;

  return (
    <>
      <div className="dash-head">
        <div>
          <h1 className="page-title">Staff dashboard</h1>
          <p className="page-sub">
            Updates every {POLL_MS / 1000}s while this tab is open.
          </p>
        </div>
        <Button type="button" variant="link" onClick={refresh}>
          Refresh now
        </Button>
      </div>

      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      {degradation && <DegradationBanner state={degradation} />}

      <div className="metrics">
        <Metric label="Conversations" value={metrics?.conversations_total ?? 0} />
        <Metric
          label="Escalated"
          value={metrics?.escalated ?? 0}
          alert={(metrics?.escalated ?? 0) > 0}
        />
        <Metric label="Resolved" value={metrics?.resolved ?? 0} />
        <Metric label="New inquiries" value={metrics?.inquiries_new ?? 0} />
      </div>

      {health && health.issues.length > 0 && (
        <Section
          title="Knowledge base needs attention"
          hint={
            "Checked " + health.policies_checked + " policies and " +
            health.documents_checked + " documents. Entries the assistant " +
            "cannot answer from reliably are listed below."
          }
        >
          <div className="issue-list">
            {health.issues.map((issue, idx) => (
              <div
                key={issue.source + issue.source_id + issue.code + idx}
                className={"issue" + (issue.severity === "low" ? " issue-low" : "")}
              >
                <div className="issue-head">
                  <span className="issue-title">{issue.title}</span>
                  <span className="pill pill-new">{issue.source}</span>
                  {issue.severity === "high" && (
                    <span className="row-waiting">may cause wrong answers</span>
                  )}
                </div>
                <div>{issue.message}</div>
                <div className="issue-excerpt">currently: {issue.excerpt || "(empty)"}</div>
              </div>
            ))}
          </div>
          <p className="section-hint" style={{ marginTop: ".6rem" }}>
            <a href="/setup">Edit policies in resort setup →</a>
          </p>
        </Section>
      )}

      <Section
        title="Conversations"
        hint="Escalated conversations are pinned to the top."
      >
        <div className="filters">
          <Button
            type="button"
            className={convoFilter === "" ? "btn-on" : undefined}
            onClick={() => setConvoFilter("")}
          >
            All
          </Button>
          {CONVERSATION_STATUSES.map((s) => (
            <Button
              key={s}
              type="button"
              className={convoFilter === s ? "btn-on" : undefined}
              onClick={() => setConvoFilter(s)}
            >
              {s}
            </Button>
          ))}
        </div>

        {conversations.length === 0 ? (
          <p className="empty">No conversations yet.</p>
        ) : (
          <div className="rows">
            {conversations.map((c) => (
              <a
                key={c.id}
                className={"row-card" + (c.awaiting_staff ? " row-urgent" : "")}
                href={`/staff/conversations/${c.id}`}
              >
                <div className="row-top">
                  <span className="row-id">#{c.id}</span>
                  <StatusPill status={c.status} />
                  {c.awaiting_staff && (
                    <span className="row-waiting">waiting for staff</span>
                  )}
                  <span className="row-meta">
                    {c.channel} · {c.message_count} msg ·{" "}
                    {shortDateTime(c.last_message_at ?? c.started_at)}
                  </span>
                </div>
                {c.last_message_preview && (
                  <div className="row-preview">{c.last_message_preview}</div>
                )}
              </a>
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Booking inquiries"
        hint="Collected automatically from guest conversations."
      >
        {inquiries.length === 0 ? (
          <p className="empty">No booking inquiries yet.</p>
        ) : (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Check-in</th>
                  <th>Check-out</th>
                  <th>Nights</th>
                  <th>Guests</th>
                  <th>Room preference</th>
                  <th>Received</th>
                  <th>Status</th>
                  <th>Chat</th>
                </tr>
              </thead>
              <tbody>
                {inquiries.map((q) => (
                  <tr key={q.id}>
                    <td>{q.id}</td>
                    <td>{shortDate(q.check_in_date)}</td>
                    <td>{shortDate(q.check_out_date)}</td>
                    <td>{nightsBetween(q.check_in_date, q.check_out_date)}</td>
                    <td>{q.guest_count}</td>
                    <td>{q.room_type_preference || "—"}</td>
                    <td>{shortDateTime(q.created_at)}</td>
                    <td>
                      <select
                        value={q.status}
                        aria-label={`Status for inquiry ${q.id}`}
                        onChange={(e) =>
                          changeInquiry(q.id, e.target.value as InquiryStatus)
                        }
                      >
                        {INQUIRY_STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <a href={`/staff/conversations/${q.conversation_id}`}>
                        #{q.conversation_id}
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}

function Metric({
  label,
  value,
  alert,
}: {
  label: string;
  value: number;
  alert?: boolean;
}) {
  return (
    <div className={"metric" + (alert ? " metric-alert" : "")}>
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}
