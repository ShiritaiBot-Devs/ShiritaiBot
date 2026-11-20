import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";

export function MemeImage({ client, id }: { client: ApiClient; id: string }) {
  const image = useQuery({
    queryKey: ["meme-image", id],
    queryFn: () => client.memeImage(id),
    staleTime: Infinity,
    retry: false,
  });
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!image.data) return;
    const next = URL.createObjectURL(image.data);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [image.data]);
  if (image.isError) return <span className="muted">表情不可用</span>;
  return url ? <img src={url} alt={`表情 ${id}`} loading="lazy" /> : null;
}
