import { ClauseSearchPage } from "../features/clauses/ClauseSearchPage";
import { RuleReviewPage } from "../features/clauses/RuleReviewPage";
import {
  ExistingEventPage,
  NewEventPage,
} from "../features/events/NewEventPage";
import { LedgerPage } from "../features/ledger/LedgerPage";
import { ClaimCasePage } from "../features/claims/ClaimCasePage";
import { ClaimListPage } from "../features/claims/ClaimListPage";
import { EventResultPage } from "../features/results/EventResultPage";

function routeMemberId(): string | undefined {
  const match = window.location.pathname.match(
    /^\/app\/members\/([^/]+)\/ledger\/?$/,
  );
  return match ? decodeURIComponent(match[1]) : undefined;
}

function routeEventId(): string | undefined {
  const match = window.location.pathname.match(/^\/app\/events\/([^/]+)\/?$/);
  return match ? decodeURIComponent(match[1]) : undefined;
}

function routeEventResult(): { eventId: string; version: number } | undefined {
  const match = window.location.pathname.match(
    /^\/app\/events\/([^/]+)\/result\/([1-9][0-9]*)\/?$/,
  );
  if (!match) return undefined;
  return {
    eventId: decodeURIComponent(match[1]),
    version: Number(match[2]),
  };
}

function routeClaimId(): string | undefined {
  const match = window.location.pathname.match(/^\/app\/claims\/([^/]+)\/?$/);
  return match ? decodeURIComponent(match[1]) : undefined;
}

export function AppRoutes() {
  const eventResult = routeEventResult();
  if (eventResult) {
    return (
      <EventResultPage
        eventId={eventResult.eventId}
        version={eventResult.version}
      />
    );
  }
  if (/^\/app\/claims\/trash\/?$/.test(window.location.pathname)) {
    return <ClaimListPage deletedOnly />;
  }
  const claimId = routeClaimId();
  if (claimId) return <ClaimCasePage claimId={claimId} />;
  if (/^\/app\/claims\/?$/.test(window.location.pathname)) {
    return <ClaimListPage />;
  }
  if (/^\/app\/events\/new\/?$/.test(window.location.pathname)) {
    return <NewEventPage />;
  }
  const eventId = routeEventId();
  if (eventId && eventId !== "new") {
    return <ExistingEventPage eventId={eventId} />;
  }
  if (/^\/app\/clauses\/search\/?$/.test(window.location.pathname)) {
    return <ClauseSearchPage />;
  }
  if (/^\/app\/clauses\/review\/?$/.test(window.location.pathname)) {
    return <RuleReviewPage />;
  }
  return <LedgerPage memberId={routeMemberId()} />;
}
