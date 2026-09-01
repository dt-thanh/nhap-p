import React, { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { getMePermissions } from "../api/endpoints";

/**
 * Do not mount an Advisor Analysis page until the server-derived capability is
 * known.  This prevents unauthorized personas from issuing governance calls
 * merely by entering a deep link.
 */
export default function AdvisorAnalysisRoute({ capability }) {
  const [allowed, setAllowed] = useState(null);

  useEffect(() => {
    let active = true;
    getMePermissions()
      .then((permissions) => {
        if (active) setAllowed(Boolean(permissions?.capabilities?.[capability]));
      })
      .catch(() => {
        if (active) setAllowed(false);
      });
    return () => { active = false; };
  }, [capability]);

  if (allowed === null) return null;
  return allowed ? <Outlet /> : <Navigate to="/overview" replace />;
}
