// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import parser from '@typescript-eslint/parser';

const root = process.env.LABEL_REGISTRY_ROOT
  ? path.resolve(process.env.LABEL_REGISTRY_ROOT)
  : path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const consumers = ['docusaurus.config.js', 'sidebars.js', 'src/data/hubCards.tsx'];
const failures = [];
const sources = new Map();

function getPropertyName(property) {
  if (property.key.type === 'Identifier') {
    return property.key.name;
  }
  return typeof property.key.value === 'string' ? property.key.value : null;
}

function getObjectProperty(object, name) {
  return object.properties.find(
    (property) => property.type === 'Property' && getPropertyName(property) === name,
  );
}

function getStaticString(node) {
  if (node?.type === 'Literal' && typeof node.value === 'string') {
    return node.value;
  }
  if (node?.type === 'TemplateLiteral' && node.expressions.length === 0) {
    return node.quasis[0]?.value.cooked ?? '';
  }
  return null;
}

function getDirectRegistryKey(node) {
  if (node?.type !== 'MemberExpression' || node.object.type !== 'Identifier' || node.object.name !== 'labelRegistry') {
    return null;
  }
  return node.computed ? getStaticString(node.property) : node.property.name;
}

function isInsideFunction(ancestors, functionName, parameterName) {
  return ancestors.some(
    (ancestor) =>
      ancestor.type === 'FunctionDeclaration' &&
      ancestor.id?.name === functionName &&
      ancestor.params[0]?.type === 'Identifier' &&
      ancestor.params[0].name === parameterName,
  );
}

function isInsideNavigationMap(ancestors, navigationName) {
  return ancestors.some(
    (ancestor) =>
      ancestor.type === 'CallExpression' &&
      ancestor.callee.type === 'MemberExpression' &&
      ancestor.callee.object.type === 'Identifier' &&
      ancestor.callee.object.name === navigationName &&
      ancestor.callee.property.type === 'Identifier' &&
      ancestor.callee.property.name === 'map' &&
      ['ArrowFunctionExpression', 'FunctionExpression'].includes(ancestor.arguments[0]?.type) &&
      ancestor.arguments[0].params[0]?.type === 'ObjectPattern' &&
      ancestor.arguments[0].params[0].properties.some(
        (property) =>
          property.type === 'Property' &&
          property.value.type === 'Identifier' &&
          property.value.name === 'label',
      ),
  );
}

function isRegistryExpression(node, ancestors, projectionState) {
  if (getDirectRegistryKey(node)) {
    return true;
  }
  if (
    node.type === 'MemberExpression' &&
    !node.computed &&
    node.object.type === 'Identifier' &&
    node.object.name === 'item' &&
    node.property.type === 'Identifier' &&
    node.property.name === 'label'
  ) {
    return projectionState.sharedNavigation && isInsideFunction(ancestors, 'toSidebarItem', 'item');
  }
  if (node.type === 'Identifier' && node.name === 'label') {
    return isInsideNavigationMap(ancestors, 'tierNavigation');
  }
  return false;
}

function walk(node, visit, parent = null, ancestors = []) {
  if (!node || typeof node !== 'object') {
    return;
  }

  visit(node, parent, ancestors);
  const childAncestors = [...ancestors, node];
  for (const [key, value] of Object.entries(node)) {
    if (['comments', 'loc', 'parent', 'range', 'tokens'].includes(key)) {
      continue;
    }
    if (Array.isArray(value)) {
      value.forEach((child) => walk(child, visit, node, childAncestors));
    } else if (value && typeof value === 'object') {
      walk(value, visit, node, childAncestors);
    }
  }
}

function parse(relativePath, contents) {
  try {
    return parser.parse(contents, {
      ecmaFeatures: { jsx: true },
      ecmaVersion: 'latest',
      loc: true,
      sourceType: 'module',
    });
  } catch (error) {
    failures.push(`${relativePath}: could not parse: ${error.message}`);
    return null;
  }
}

function isGovernedProperty(relativePath, property, object) {
  const propertyName = getPropertyName(property);
  if (relativePath === 'sidebars.js') {
    return propertyName === 'label';
  }
  if (relativePath === 'src/data/hubCards.tsx') {
    return propertyName === 'title' || propertyName === 'supertitle';
  }
  if (relativePath !== 'docusaurus.config.js' || propertyName !== 'label') {
    return false;
  }

  const type = getStaticString(getObjectProperty(object, 'type')?.value);
  return (
    Boolean(getObjectProperty(object, 'to')) ||
    ['docSidebar', 'dropdown'].includes(type) ||
    getDirectRegistryKey(property.value) !== null
  );
}

function collectRegistryKeys(ast) {
  const keys = new Set();
  walk(ast, (node) => {
    if (node.type !== 'VariableDeclarator' || node.id.type !== 'Identifier' || node.id.name !== 'labelRegistry') {
      return;
    }
    const registry = node.init?.type === 'CallExpression' ? node.init.arguments[0] : node.init;
    if (registry?.type === 'ObjectExpression') {
      registry.properties.forEach((property) => {
        if (property.type === 'Property') {
          keys.add(getPropertyName(property));
        }
      });
    }
  });
  return keys;
}

function collectNavigationRegistryReferences(ast, declarationName) {
  const references = new Set();
  walk(ast, (node) => {
    if (node.type !== 'VariableDeclarator' || node.id.type !== 'Identifier' || node.id.name !== declarationName) {
      return;
    }
    walk(node.init, (child) => {
      const key = getDirectRegistryKey(child);
      if (key) {
        references.add(key);
      }
    });
  });
  return references;
}

function hasNavigationMap(ast, navigationName, callbackName = null) {
  let found = false;
  walk(ast, (node) => {
    if (
      node.type === 'CallExpression' &&
      node.callee.type === 'MemberExpression' &&
      node.callee.object.type === 'Identifier' &&
      node.callee.object.name === navigationName &&
      node.callee.property.type === 'Identifier' &&
      node.callee.property.name === 'map' &&
      (callbackName === null || node.arguments[0]?.type === 'Identifier' && node.arguments[0].name === callbackName)
    ) {
      found = true;
    }
  });
  return found;
}

for (const relativePath of consumers) {
  const absolutePath = path.join(root, relativePath);
  if (!fs.existsSync(absolutePath)) {
    failures.push(`${relativePath}: expected consumer file is missing`);
    continue;
  }

  const contents = fs.readFileSync(absolutePath, 'utf8');
  sources.set(relativePath, { ast: parse(relativePath, contents), contents });
  if (!contents.includes('labelRegistry')) {
    failures.push(`${relativePath}: does not reference labelRegistry`);
  }
}

const registryPath = 'src/data/labelRegistry.cjs';
const registryContents = fs.readFileSync(path.join(root, registryPath), 'utf8');
const registryAst = parse(registryPath, registryContents);
sources.set(registryPath, { ast: registryAst, contents: registryContents });

const projectionState = {
  sharedNavigation: hasNavigationMap(sources.get('sidebars.js')?.ast, 'sharedNavigation', 'toSidebarItem'),
  tierConfig: hasNavigationMap(sources.get('docusaurus.config.js')?.ast, 'tierNavigation'),
  tierSidebar: hasNavigationMap(sources.get('sidebars.js')?.ast, 'tierNavigation'),
};

if (!projectionState.sharedNavigation) {
  failures.push('sidebars.js: sharedNavigation must be projected through map(toSidebarItem)');
}
if (!projectionState.tierConfig || !projectionState.tierSidebar) {
  failures.push('tierNavigation must be projected by both docusaurus.config.js and sidebars.js');
}

const governedReferences = new Set();

for (const [relativePath, source] of sources) {
  if (!source.ast || relativePath === registryPath) {
    continue;
  }

  walk(source.ast, (node, parent, ancestors) => {
    if (node.type !== 'Property' || parent?.type !== 'ObjectExpression') {
      return;
    }
    if (!isGovernedProperty(relativePath, node, parent)) {
      return;
    }
    const registryKey = getDirectRegistryKey(node.value);
    if (registryKey) {
      governedReferences.add(registryKey);
    } else if (!isRegistryExpression(node.value, ancestors, projectionState)) {
      failures.push(
        `${relativePath}:${node.loc.start.line}: governed ${getPropertyName(node)} must use a direct registry member or shared navigation data`,
      );
    }
  });

  walk(source.ast, (node, parent, ancestors) => {
    const registryKey = getDirectRegistryKey(node);
    if (!registryKey) {
      return;
    }
    const object = ancestors.at(-2);
    if (
      parent?.type !== 'Property' ||
      parent.value !== node ||
      object?.type !== 'ObjectExpression' ||
      !isGovernedProperty(relativePath, parent, object)
    ) {
      failures.push(`${relativePath}:${node.loc.start.line}: labelRegistry.${registryKey} is outside a governed label field`);
    }
  });
}

if (registryAst) {
  const registryKeys = collectRegistryKeys(registryAst);
  const references = new Set(governedReferences);
  if (projectionState.tierConfig && projectionState.tierSidebar) {
    collectNavigationRegistryReferences(registryAst, 'tierNavigation').forEach((key) => references.add(key));
  }
  if (projectionState.sharedNavigation) {
    collectNavigationRegistryReferences(registryAst, 'sharedNavigation').forEach((key) => references.add(key));
  }

  for (const key of references) {
    if (!registryKeys.has(key)) {
      failures.push(`${registryPath}: unknown labelRegistry key ${key}`);
    }
  }

  for (const key of registryKeys) {
    if (!references.has(key)) {
      failures.push(`${registryPath}: labelRegistry.${key} is not consumed`);
    }
  }
}

if (failures.length > 0) {
  console.error('Label-registry consistency check failed:');
  failures.forEach((failure) => console.error(`  - ${failure}`));
  process.exit(1);
}

console.log(`Label-registry consistency check passed (${consumers.length} consumers).`);