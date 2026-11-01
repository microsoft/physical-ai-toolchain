import { visit } from 'unist-util-visit';

export default function rehypeFootnoteLinks() {
  return (tree) => {
    visit(tree, 'element', (node) => {
      if (node.tagName !== 'a' || !Object.hasOwn(node.properties ?? {}, 'dataFootnoteBackref')) {
        return;
      }

      const label = node.properties.ariaLabel;
      if (typeof label === 'string' && label.trim()) {
        node.children = [{ type: 'text', value: label }];
      }
    });
  };
}
