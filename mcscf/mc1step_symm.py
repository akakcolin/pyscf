#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

import numpy
import pyscf.lib.logger as logger
import pyscf.gto
from pyscf import symm
from pyscf.mcscf import mc1step
from pyscf.mcscf import mc2step
from pyscf import scf
from pyscf import ao2mo
from pyscf import fci
from pyscf.tools.mo_mapping import mo_1to1map


class CASSCF(mc1step.CASSCF):
    def __init__(self, mf, ncas, nelecas, ncore=None, frozen=[]):
        assert(mf.mol.symmetry)
        self.orbsym = []
        mc1step.CASSCF.__init__(self, mf, ncas, nelecas, ncore, frozen)

    def mc1step(self, mo_coeff=None, ci0=None, macro=None, micro=None,
                callback=None):
        return self.kernel(mo_coeff, ci0, macro, micro, callback,
                           mc1step.kernel)

    def mc2step(self, mo_coeff=None, ci0=None, macro=None, micro=1,
                callback=None):
        return self.kernel(mo_coeff, ci0, macro, micro, callback,
                           mc2step.kernel)

    def kernel(self, mo_coeff=None, ci0=None, macro=None, micro=None,
               callback=None, _kern=None):
        if mo_coeff is None:
            mo_coeff = self.mo_coeff
        else:
            self.mo_coeff = mo_coeff
        if macro is None: macro = self.max_cycle_macro
        if micro is None: micro = self.max_cycle_micro
        if callback is None: callback = self.callback
        if _kern is None: _kern = mc1step.kernel

        if self.verbose > logger.QUIET:
            pyscf.gto.mole.check_sanity(self, self._keys, self.stdout)

        self.mol.check_sanity(self)
        self.dump_flags()

        #irrep_name = self.mol.irrep_name
        irrep_name = self.mol.irrep_id
        try:
            self.orbsym = symm.label_orb_symm(self.mol, irrep_name,
                                              self.mol.symm_orb, mo_coeff,
                                              s=self._scf.get_ovlp())
        except ValueError:
            logger.warn(self, 'mc1step_symm symmetrizes input orbitals')
            s = self._scf.get_ovlp()
            mo_coeff = symm.symmetrize_orb(self.mol, mo_coeff, s=s)
            diag = numpy.einsum('ki,ki->i', mo_coeff, numpy.dot(s, mo_coeff))
            mo_coeff = numpy.einsum('ki,i->ki', mo_coeff, 1/numpy.sqrt(diag))
            self.orbsym = symm.label_orb_symm(self.mol, irrep_name,
                                              self.mol.symm_orb, mo_coeff, s=s)

        if not hasattr(self.fcisolver, 'orbsym') or \
           not self.fcisolver.orbsym:
            ncore = self.ncore
            nocc = self.ncore + self.ncas
            self.fcisolver.orbsym = self.orbsym[ncore:nocc]
        logger.debug(self, 'Active space irreps %s', str(self.fcisolver.orbsym))

        self.converged, self.e_tot, e_cas, self.ci, self.mo_coeff = \
                _kern(self, mo_coeff,
                      tol=self.conv_tol, conv_tol_grad=self.conv_tol_grad,
                      macro=macro, micro=micro,
                      ci0=ci0, callback=callback, verbose=self.verbose)
        logger.note(self, 'CASSCF energy = %.15g', self.e_tot)
        return self.e_tot, e_cas, self.ci, self.mo_coeff

    def gen_g_hop(self, mo, u, casdm1, casdm2, eris):
        casdm1 = _symmetrize(casdm1, self.orbsym[self.ncore:self.ncore+self.ncas],
                             self.mol.groupname)
        g_orb, gorb_op, h_op, h_diag = \
                mc1step.gen_g_hop(self, mo, u, casdm1, casdm2, eris)
        g_orb = _symmetrize(self.unpack_uniq_var(g_orb), self.orbsym,
                            self.mol.groupname)
        h_diag = _symmetrize(self.unpack_uniq_var(h_diag), self.orbsym,
                             self.mol.groupname)
        def sym_h_op(x):
            hx = h_op(x)
            hx = _symmetrize(self.unpack_uniq_var(hx), self.orbsym,
                             self.mol.groupname)
            return self.pack_uniq_var(hx)
        def sym_gorb_op(x):
            g = gorb_op(x)
            g = _symmetrize(self.unpack_uniq_var(g), self.orbsym,
                            self.mol.groupname)
            return self.pack_uniq_var(g)
        return self.pack_uniq_var(g_orb), sym_gorb_op, sym_h_op, \
               self.pack_uniq_var(h_diag)

    def update_rotate_matrix(self, dx, u0=1):
        dr = self.unpack_uniq_var(dx)
        dr = _symmetrize(dr, self.orbsym, self.mol.groupname)
        return numpy.dot(u0, mc1step.expmat(dr))

    def _eig(self, mat, b0, b1):
        orbsym = numpy.array(self.orbsym[b0:b1])
        norb = mat.shape[0]
        e = numpy.zeros(norb)
        c = numpy.zeros((norb,norb))
        for i0 in set(orbsym):
            lst = numpy.where(orbsym == i0)[0]
            if len(lst) > 0:
                w, v = scf.hf.eig(mat[lst[:,None],lst], None)
                e[lst] = w
                c[lst[:,None],lst] = v
        return e, c

def _symmetrize(mat, orbsym, groupname):
    mat1 = numpy.zeros_like(mat)
    orbsym = numpy.array(orbsym)
    for i0 in set(orbsym):
        lst = numpy.where(orbsym == i0)[0]
        mat1[lst[:,None],lst] = mat[lst[:,None],lst]
    return mat1


if __name__ == '__main__':
    from pyscf import gto
    from pyscf import scf
    import pyscf.fci
    from pyscf.mcscf import addons

    mol = gto.Mole()
    mol.verbose = 0
    mol.output = None

    mol.atom = [
        ['O', ( 0., 0.    , 0.   )],
        ['H', ( 0., -0.757, 0.587)],
        ['H', ( 0., 0.757 , 0.587)],]
    mol.basis = {'H': 'cc-pvdz',
                 'O': 'cc-pvdz',}
    mol.symmetry = 1
    mol.build()

    m = scf.RHF(mol)
    ehf = m.scf()
    mc = CASSCF(m, 6, 4)
    mc.fcisolver = pyscf.fci.solver(mol)
    mc.verbose = 4
    mo = addons.sort_mo(mc, m.mo_coeff, (3,4,6,7,8,9), 1)
    emc = mc.mc1step(mo)[0]
    print(ehf, emc, emc-ehf)
    #-76.0267656731 -76.0873922924 -0.0606266193028
    print(emc - -76.0873923174, emc - -76.0926176464)

    mc = CASSCF(m, 6, (3,1))
    #mc.fcisolver = pyscf.fci.direct_spin1
    mc.fcisolver = pyscf.fci.solver(mol, False)
    mc.verbose = 4
    emc = mc.mc1step(mo)[0]
    print(emc - -75.7155632535814)

    mc = CASSCF(m, 6, (3,1))
    mc.fcisolver.wfnsym = 'B1'
    mc.verbose = 4
    emc = mc.mc1step(mo)[0]
    print(emc - -75.6406597705231)
