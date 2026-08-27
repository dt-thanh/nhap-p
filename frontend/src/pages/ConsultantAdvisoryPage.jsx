import React from "react";
import { useParams } from "react-router-dom";
import AgentPage from "./AgentPage";

// The existing AgentPage owns the live recommendation polling and HITL guards.
// This route gives consultant workflows a stable URL without duplicating that logic.
export default function ConsultantAdvisoryPage() {
  const { consultantId } = useParams();
  return <AgentPage consultantId={consultantId} />;
}
