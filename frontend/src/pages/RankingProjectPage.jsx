import React from "react";
import { useParams } from "react-router-dom";
import RankingPage from "./RankingPage";

export default function RankingProjectPage() {
  const { projectId } = useParams();
  return <RankingPage projectExternalId={projectId} />;
}
