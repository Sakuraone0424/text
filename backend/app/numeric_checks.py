"""Bounded rational arithmetic, not arbitrary Python execution or scientific proof."""

import ast
from fractions import Fraction


def value(expression):
    if len(expression) > 300:
        raise ValueError("expression_too_large")
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 50:
        raise ValueError("expression_too_complex")

    def visit(node, depth=0):
        if depth > 10:
            raise ValueError("expression_too_deep")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = Fraction(str(node.value))
        elif isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)
        ):
            result = visit(node.operand, depth + 1) * (
                1 if isinstance(node.op, ast.UAdd) else -1
            )
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
        ):
            a, b = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                result = a + b
            elif isinstance(node.op, ast.Sub):
                result = a - b
            elif isinstance(node.op, ast.Mult):
                result = a * b
            elif isinstance(node.op, ast.Div):
                result = a / b
            else:
                if b.denominator != 1 or abs(b) > 6:
                    raise ValueError("exponent_out_of_bounds")
                result = a ** int(b)
        else:
            raise ValueError("unsupported_expression")
        if abs(result) > 10**12 or result.denominator > 10**12:
            raise ValueError("numeric_bound_exceeded")
        return result

    return visit(tree.body)


def verify(expression, expected):
    try:
        return value(expression) == value(expected)
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return False
