"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { LoadingState } from "@/components/ui";

// Old address for a client folder; the folder page now lives under /folders.
export default function LegacyFolderRedirect() {
  const params = useParams();
  const router = useRouter();
  const folderId = params?.folderId as string;

  useEffect(() => {
    router.replace(folderId ? `/folders/${folderId}` : "/folders");
  }, [folderId, router]);

  return <LoadingState label="Opening folder..." />;
}
