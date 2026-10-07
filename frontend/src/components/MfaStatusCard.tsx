"use client";

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { fetchMfaStatus } from "@/lib/api";
import { Badge, Section } from "@/components/ui";

/** Shows whether the signed-in person's account is protected by two-step verification. */
export function MfaStatusCard() {
  const [status, setStatus] = useState<{ required: boolean; enabled: boolean; recovery_codes_left: number } | null>(null);

  useEffect(() => {
    fetchMfaStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  if (!status || (!status.required && !status.enabled)) return null;

  return (
    <Section
      icon={ShieldCheck}
      title={
        <span className="inline-flex items-center gap-2">
          Two-step verification
          <Badge tone={status.enabled ? "green" : "amber"}>{status.enabled ? "On" : "Not set up"}</Badge>
        </span>
      }
      description={
        status.enabled
          ? `When you sign in, you also type a 6-digit code from your authenticator app. You have ${status.recovery_codes_left} recovery code${status.recovery_codes_left === 1 ? "" : "s"} left. If you lose your phone, a JTS administrator can reset it.`
          : "Admin accounts need two-step verification. You will be asked to set it up the next time you sign in."
      }
    >
      <p className="text-xs text-gray-500">It protects your account even if someone learns your password.</p>
    </Section>
  );
}
