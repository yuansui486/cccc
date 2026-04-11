import { describe, expect, it } from "vitest";
import {
  getAttachmentBlobName,
  getAttachmentAwareMessageText,
  hasBlobAttachmentPath,
  isImageAttachment,
  isRedundantWecomImagePlaceholder,
  normalizeAttachmentPath,
  isSvgAttachment,
} from "../../src/utils/messageAttachments";

describe("messageAttachments", () => {
  it("recognizes SVG attachments from mime type", () => {
    const attachment = {
      kind: "file",
      path: "state/blobs/sha_demo.svg",
      title: "demo.svg",
      mime_type: "image/svg+xml",
    };
    expect(isImageAttachment(attachment)).toBe(true);
    expect(isSvgAttachment(attachment)).toBe(true);
  });

  it("falls back to kind and extension when mime type is missing", () => {
    const attachment = {
      kind: "image",
      path: "state/blobs/sha_demo.svg",
      title: "demo.svg",
      mime_type: "",
    };
    expect(isImageAttachment(attachment)).toBe(true);
    expect(isSvgAttachment(attachment)).toBe(true);
  });

  it("does not treat generic files as images", () => {
    const attachment = {
      kind: "file",
      path: "state/blobs/sha_demo.txt",
      title: "demo.txt",
      mime_type: "text/plain",
    };
    expect(isImageAttachment(attachment)).toBe(false);
    expect(isSvgAttachment(attachment)).toBe(false);
  });

  it("hides redundant wecom image placeholders when image attachments exist", () => {
    const attachment = {
      kind: "image",
      path: "state/blobs/sha_demo.png",
      title: "demo.png",
      mime_type: "image/png",
    };
    expect(isRedundantWecomImagePlaceholder("[image]", [attachment], "wecom")).toBe(true);
    expect(isRedundantWecomImagePlaceholder("[file: unknown]", [attachment], "wecom")).toBe(true);
  });

  it("keeps non-wecom or non-image placeholder text visible", () => {
    const imageAttachment = {
      kind: "image",
      path: "state/blobs/sha_demo.png",
      title: "demo.png",
      mime_type: "image/png",
    };
    const fileAttachment = {
      kind: "file",
      path: "state/blobs/sha_demo.txt",
      title: "demo.txt",
      mime_type: "text/plain",
    };
    expect(isRedundantWecomImagePlaceholder("[image]", [imageAttachment], "telegram")).toBe(false);
    expect(isRedundantWecomImagePlaceholder("需要人工确认", [imageAttachment], "wecom")).toBe(false);
    expect(isRedundantWecomImagePlaceholder("[image]", [fileAttachment], "wecom")).toBe(false);
  });

  it("suppresses redundant wecom image placeholder text in display rendering", () => {
    const attachment = {
      kind: "image",
      path: "state/blobs/sha_demo.png",
      title: "demo.png",
      mime_type: "image/png",
    };
    expect(getAttachmentAwareMessageText("[image]", [attachment], "wecom")).toBe("");
    expect(getAttachmentAwareMessageText("需要人工确认", [attachment], "wecom")).toBe("需要人工确认");
  });

  it("normalizes Windows-style blob paths so WeCom attachments remain renderable", () => {
    const attachment = {
      kind: "image",
      path: "state\\blobs\\sha_demo.png",
      title: "demo.png",
      mime_type: "image/png",
    };

    expect(normalizeAttachmentPath(attachment.path)).toBe("state/blobs/sha_demo.png");
    expect(hasBlobAttachmentPath(attachment)).toBe(true);
    expect(getAttachmentBlobName(attachment)).toBe("sha_demo.png");
    expect(isImageAttachment(attachment)).toBe(true);
  });
});
