import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Uncaught error in widget sandbox:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div className="w-full h-full flex flex-col items-center justify-center p-4 bg-red-950/20 border border-red-900/30 rounded-xl text-center">
          <div className="w-8 h-8 rounded-full bg-red-500/10 flex items-center justify-center mb-2">
            <svg className="w-4 h-4 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h3 className="text-xs font-semibold text-red-200">Widget Crashed</h3>
          <p className="text-[10px] text-red-400/80 mt-1 max-w-xs break-words font-mono">
            {this.state.error?.message || "An unexpected error occurred during rendering."}
          </p>
          <button
            onClick={() => {
              this.setState({ hasError: false, error: null });
            }}
            className="mt-3 px-2.5 py-1 text-[10px] font-semibold bg-red-500/20 hover:bg-red-500/30 text-red-300 rounded border border-red-500/30 cursor-pointer transition-colors"
          >
            Reset Widget
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
