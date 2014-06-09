from __future__ import absolute_import, division, print_function

from multipledispatch import dispatch
import sys
from operator import itemgetter
import operator
from toolz import compose
from toolz.curried import get

from blaze.expr.table import *
from blaze.expr.table import count as Count
from . import core, python
from .python import compute, rowfunc, RowWise
from ..compatibility import builtins
from ..expr import table

try:
    from itertools import compress, chain
    import pyspark
    from pyspark.rdd import RDD
except ImportError:
    #Create a dummy RDD for py 2.6
    class Dummy(object):
        pass
    pyspark = Dummy()
    pyspark.rdd = Dummy()
    RDD = Dummy

# PySpark adds a SIGCHLD signal handler, but that breaks other packages, so we
# remove it
# See https://issues.apache.org/jira/browse/SPARK-1394
try:
    import signal
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
except:
    pass


@dispatch(RowWise, RDD)
def compute(t, rdd):
    rdd = compute(t.parent, rdd)
    func = rowfunc(t)
    return rdd.map(func)


@dispatch(TableSymbol, RDD)
def compute(t, s):
    return s


@dispatch(Selection, RDD)
def compute(t, rdd):
    rdd = compute(t.parent, rdd)
    predicate = rowfunc(t.predicate)
    return rdd.filter(predicate)


rdd_reductions = {
        table.sum: RDD.sum,
        table.min: RDD.min,
        table.max: RDD.max,
        table.count: RDD.count,
        table.mean: RDD.mean,
        table.var: RDD.variance,
        table.std: RDD.stdev,
        table.nunique: compose(RDD.count, RDD.distinct)}


@dispatch(tuple(rdd_reductions), RDD)
def compute(t, rdd):
    reduction = rdd_reductions[type(t)]
    return reduction(compute(t.parent, rdd))


@dispatch(table.any, RDD)
def compute(t, rdd):
    rdd = compute(t.parent, rdd)
    return rdd.fold(False, operator.or_)


@dispatch(table.all, RDD)
def compute(t, rdd):
    rdd = compute(t.parent, rdd)
    return rdd.fold(True, operator.and_)


@dispatch(Head, RDD)
def compute(t, rdd):
    rdd = compute(t.parent, rdd)
    return rdd.take(t.n)


@dispatch(Sort, RDD)
def compute(t, rdd):
    rdd = compute(t.parent, rdd)
    func = rowfunc(t[t.column])
    return (rdd.keyBy(func)
                .sortByKey(ascending=t.ascending)
                .map(lambda x: x[1]))


@dispatch(Join, RDD, RDD)
def compute(t, lhs, rhs):
    lhs = compute(t.lhs, lhs)
    rhs = compute(t.rhs, rhs)

    col_idx_lhs = t.lhs.columns.index(t.on_left)
    col_idx_rhs = t.rhs.columns.index(t.on_right)

    lhs = lhs.keyBy(lambda x: x[col_idx_lhs])
    rhs = rhs.keyBy(lambda x: x[col_idx_rhs])

    # Calculate the indices we want in the joined table
    columns = t.lhs.columns + t.rhs.columns
    repeated_index = len(columns) - columns[::-1].index(t.on_right) - 1
    wanted = list(range(len(columns)))
    wanted.pop(repeated_index)
    getter = get(wanted)
    reassemble = lambda x: getter(x[1][0] + x[1][1])

    return lhs.join(rhs).map(reassemble)


python_reductions = {
              table.sum: builtins.sum,
              table.count: builtins.len,
              table.max: builtins.max,
              table.min: builtins.min,
              table.any: builtins.any,
              table.all: builtins.all,
              table.mean: python._mean,
              table.var: python._var,
              table.std: python._std,
              table.nunique: lambda x: len(set(x))}


@dispatch(By, RDD)
def compute(t, rdd):
    rdd = compute(t.parent, rdd)
    try:
        reduction = python_reductions[type(t.apply)]
    except KeyError:
        raise NotImplementedError("By only implemented for common reductions."
                                  "\nGot %s" % type(t.apply))

    grouper = rowfunc(t.grouper)
    pre = rowfunc(t.apply.parent)

    return (rdd.map(lambda x: (grouper(x), pre(x)))
               .groupByKey()
               .map(lambda x: (x[0], reduction(x[1]))))


@dispatch((Label, ReLabel), RDD)
def compute(t, rdd):
    return compute(t.parent, rdd)
