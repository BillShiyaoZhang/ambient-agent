import React from "react";

import { IsolatedSandboxWidget } from "./IsolatedSandboxWidget";
import {
  PixelSandboxWidget,
  type SandboxWidgetProps,
} from "./PixelSandboxWidget";

export type {
  SandboxWidgetProps,
  WidgetPresentationContext,
} from "./PixelSandboxWidget";
export { PixelSandboxWidget } from "./PixelSandboxWidget";


export const SandboxWidget: React.FC<SandboxWidgetProps> = (props) => {
  if (import.meta.env.VITE_WIDGET_UI_TRANSPORT === "pixels") {
    return <PixelSandboxWidget {...props} />;
  }
  return <IsolatedSandboxWidget {...props} />;
};
