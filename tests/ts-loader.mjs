import { access } from "node:fs/promises";
import { readFile } from "node:fs/promises";
import ts from "typescript";

// TypeScript compiles .js specifiers to .ts sources; Node's type stripper does not.
export async function resolve(specifier, context, nextResolve) {
  if (specifier.endsWith(".js") && context.parentURL?.includes("/src/")) {
    const source = new URL(specifier.replace(/\.js$/, ".ts"), context.parentURL);
    try {
      await access(source);
      return nextResolve(source.href, context);
    } catch { /* resolve normal JS modules unchanged */ }
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.startsWith("file:") && /\.tsx?$/.test(url)) {
    const input = await readFile(new URL(url), "utf8");
    const { outputText } = ts.transpileModule(input, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
      fileName: url,
    });
    return { format: "module", source: outputText, shortCircuit: true };
  }
  return nextLoad(url, context);
}