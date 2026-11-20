import ReactMarkdown, { type Options } from "react-markdown";
import { memo } from "react";
import type {} from "remark-parse";
import remarkGfm from "remark-gfm";
import { findAndReplace } from "mdast-util-find-and-replace";
import type { ApiClient } from "../api/client";
import { MemeImage } from "./MemeImage";

const plugins: Options["remarkPlugins"] = [
  remarkGfm,
  function memeMarkers() {
    // Parse chat markup as text, leaving inline and fenced code untouched.
    (this.data().micromarkExtensions ??= []).push({
      disable: { null: ["htmlFlow", "htmlText"] },
    });
    return (tree) => {
      findAndReplace(tree, [
        /<(?:me){2,}>\s*(\w{1,40})\s*<\/(?:me){2,}>/gi,
        (_, id: string) => ({
          type: "image",
          url: `/api/memes/${id}/image`,
          alt: "表情",
        }),
      ]);
    };
  },
];

export default memo(function ChatMarkdown({
  client,
  text,
}: {
  client: ApiClient;
  text: string;
}) {
  return (
    <div className="chat-markdown">
      <ReactMarkdown
        remarkPlugins={plugins}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          img: ({ src, alt }) => {
            const marker =
              typeof src === "string" &&
              /^\/api\/memes\/(\w{1,40})\/image$/.exec(src);
            return marker ? (
              <MemeImage key={marker[1]} client={client} id={marker[1]} />
            ) : (
              <img
                src={src}
                alt={alt || "图片"}
                loading="lazy"
                referrerPolicy="no-referrer"
              />
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
