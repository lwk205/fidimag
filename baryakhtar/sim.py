from __future__ import division
import os
import cvode
import baryakhtar_clib as clib 
import numpy as np
from pc import FDMesh
from pc import DataSaver, DataReader
from pc import SaveVTK
from pc import Constant

import pccp.util.helper as helper

const = Constant()

class Laplace(object):
    """
    compute the exchange field.
    """
    def __init__(self, mesh):
        self.dx=mesh.dx*mesh.unit_length
        self.dy=mesh.dy*mesh.unit_length
        self.dz=mesh.dz*mesh.unit_length
        self.nx=mesh.nx
        self.ny=mesh.ny
        self.nz=mesh.nz
        self.field = np.zeros(3*mesh.nxyz)
    

    def compute_laplace_field(self, h):
        
        clib.compute_laplace_field(h, self.field,
                                      self.dx,
                                      self.dy,
                                      self.dz,
                                      self.nx,
                                      self.ny,
                                      self.nz)
        
        return self.field

class Sim(object):
    
    def __init__(self, mesh, Me, name='unnamed'):
        """Simulation object.

        *Arguments*

          mesh : a dolfin mesh

          name : the Simulation name (used for writing data files, for examples)

          pbc : Periodic boundary type: None, '1d' or '2d'

          driver : 'llg', 'sllg' or 'llg_stt'

        """
        
        self.t = 0
        self.name = name
        self.mesh = mesh
        self.nxyz = mesh.nxyz
        
        self.Me = Me
        self.nxyz_nonzero = mesh.nxyz
        self.unit_length = mesh.unit_length
        self._alpha = np.zeros(self.nxyz,dtype=np.float)
        
        self.spin = np.ones(3*self.nxyz,dtype=np.float)
        self.spin_last = np.ones(3*self.nxyz,dtype=np.float)
        self._pins = np.zeros(self.nxyz,dtype=np.int32)
        self.field = np.zeros(3*self.nxyz,dtype=np.float)
        self.dm_dt = np.zeros(3*self.nxyz,dtype=np.float)
        
        self.lap = Laplace(mesh)
        
        self.interactions=[]
        self.pin_fun = None

        self.step = 0
        
        self.saver = DataSaver(self, name+'.txt')
        
        self.saver.entities['E_total'] = {
            'unit': '<J>',
            'get': lambda sim : sim.compute_energy(),
            'header': 'E_total'}
        
        self.saver.entities['m_error'] = {
            'unit': '<>',
            'get': lambda sim : sim.compute_spin_error(),
            'header': 'm_error'}
        
        
        self.saver.update_entity_order()


        self.set_options()
        
        self.vtk=SaveVTK(self.mesh,self.spin,name=name)


    def set_options(self,rtol=1e-8,atol=1e-12, dt=1e-15):

        self.default_c = -1.0 
        self._alpha[:] = 0.1
        self.beta = 0
        self.gamma = 2.21e5

        self.do_procession = True

        self.vode = cvode.CvodeSolver(self.spin,
                                    rtol,atol,
                                    self.sundials_rhs)

        
    def set_m(self,m0=(1,0,0),normalise=True):
        
        self.spin[:] = helper.init_vector(m0,self.mesh, normalise)
        

        self.vode.set_initial_value(self.spin, self.t)
    
    def get_pins(self):
        return self._pins

    def set_pins(self,pin):
        self._pins[:] = helper.init_scalar(pin, self.mesh)
        
        for i in range(len(self._mu_s)):
            if self._mu_s[i] == 0.0:
                self._pins[i] = 1

    pins = property(get_pins, set_pins)
    
    def get_alpha(self):
        return self._alpha

    def set_alpha(self,value):
        self._alpha[:] = helper.init_scalar(value, self.mesh)
        
    alpha = property(get_alpha, set_alpha)
    

    def add(self,interaction, save_field=False):
        
        interaction.setup(self.mesh,self.spin,self.Me)
        
        #TODO: FIX
        for i in self.interactions:
            if i.name == interaction.name:
                interaction.name=i.name+'_2'
        
        self.interactions.append(interaction)
        
        energy_name = 'E_{0}'.format(interaction.name)
        self.saver.entities[energy_name] = {
            'unit': '<J>',
            'get': lambda sim:sim.get_interaction(interaction.name).compute_energy(),
            'header': energy_name}
        
        if save_field:
            fn = '{0}'.format(interaction.name)
            self.saver.entities[fn] = {
            'unit': '<>',
            'get': lambda sim:sim.get_interaction(interaction.name).average_field(),
            'header': ('%s_x'%fn, '%s_y'%fn, '%s_z'%fn)}
        
        self.saver.update_entity_order()
        
    def get_interaction(self, name):
        for interaction in self.interactions:
            if interaction.name == name:
                return interaction
        else: 
            raise ValueError("Failed to find the interaction with name '{0}', "
                             "available interactions: {1}.".format(
                        name, [x.name for x in self.interactions]))
        

    def run_until(self,t):
        
        if t <= self.t:
            if t == self.t and self.t==0.0:
                self.compute_effective_field(t)
                self.saver.save()
            return
        
        ode = self.vode
        
        self.spin_last[:] = self.spin[:]
        
        flag = ode.run_until(t)
        
        if flag < 0:
            raise Exception("Run cython run_until failed!!!")
        
        self.spin[:] = ode.y[:]

        self.t = t
        self.step += 1
        
        #update field before saving data
        self.compute_effective_field(t)
        self.saver.save()

    def update_effective_field(self, y, t):
        
        self.spin[:]=y[:]
        
        self.field[:]=0
                
        for obj in self.interactions:
            self.field += obj.compute_field(t)

    def compute_effective_field(self, t):
        
        self.field[:]=0

        for obj in self.interactions:
            self.field += obj.compute_field(t)
    
    def sundials_rhs(self, t, y, ydot):
        
        self.t = t
        
        #already synchronized when call this funciton
        #self.spin[:]=y[:]
                
        self.compute_effective_field(t)
        
        delta_h = self.lap.compute_laplace_field(self.field)
        
        clib.compute_llg_rhs_baryakhtar(ydot,
                             self.spin,
                             self.field,
                             delta_h,
                             self.alpha,
                             self.beta,
                             self._pins,
                             self.gamma,
                             self.nxyz,
                             self.do_procession)
        
        print 'hahahah', t
        #ydot[:] = self.dm_dt[:]
                
        return 0
        
    def compute_average(self):
        self.spin.shape=(3,-1)
        average=np.sum(self.spin,axis=1)/self.nxyz_nonzero
        self.spin.shape=(3*self.nxyz)
        return average

    def compute_energy(self):
        
        energy=0
        
        for obj in self.interactions:
            energy += obj.compute_energy()
            
        return energy



    def spin_at(self,i,j,k):
        nxyz=self.mesh.nxyz

        i1=self.mesh.index(i,j,k)
        i2=i1+nxyz
        i3=i2+nxyz

        #print self.spin.shape,nxy,nx,i1,i2,i3
        return np.array([self.spin[i1],
                self.spin[i2],
                self.spin[i3]])
    
    def add_monitor_at(self, i,j,k, name='p'):
        """
        Save site spin with index (i,j,k) to txt file.
        """
    
        self.saver.entities[name] = {
            'unit': '<>',
            'get': lambda sim:sim.spin_at(i,j,k),
            'header': (name+'_x',name+'_y',name+'_z')}
        
        self.saver.update_entity_order()


    def save_vtk(self):
        self.vtk.save_vtk(self.spin, step=self.step)
    
    def save_m(self):
        if not os.path.exists('%s_npys'%self.name):
            os.makedirs('%s_npys'%self.name)
        name = '%s_npys/m_%g.npy'%(self.name,self.step)
        np.save(name,self.spin)
        
    def save_skx(self):
        if not os.path.exists('%s_skx_npys'%self.name):
            os.makedirs('%s_skx_npys'%self.name)
        name = '%s_skx_npys/m_%g.npy'%(self.name,self.step)
        np.save(name,self._skx_number)

    def stat(self):
        return self.vode.stat()

    def spin_length(self):
        self.spin.shape=(3,-1)
        length=np.sqrt(np.sum(self.spin**2,axis=0))
        self.spin.shape=(-1,)
        return length
    
    def compute_spin_error(self):
        length = self.spin_length()-1.0
        length[self._pins>0] = 0 
        return np.max(abs(length))
    
    def compute_dmdt(self, dt):
        m0 = self.spin_last
        m1 = self.spin
        dm = (m1 - m0).reshape((3, -1))
        max_dm = np.max(np.sqrt(np.sum(dm**2, axis=0))) 
        max_dmdt = max_dm / dt
        return max_dmdt
    
    def relax(self, dt=1.0, stopping_dmdt=0.01, max_steps=1000, save_m_steps=100, save_vtk_steps=100):
                 
        for i in range(0,max_steps+1):
            
            cvode_dt = self.vode.get_current_step()
            
            increment_dt = dt
            
            if cvode_dt > dt:
                increment_dt = cvode_dt

            self.run_until(self.t+increment_dt)
            
            if save_vtk_steps is not None:
                if i%save_vtk_steps==0:
                    self.save_vtk()
            if save_m_steps is not None:
                if i%save_m_steps==0:
                    self.save_m()
            
            dmdt = self.compute_dmdt(increment_dt)
            
            print 'step=%d, time=%g, max_dmdt=%g ode_step=%g'%(self.step, self.t, dmdt, cvode_dt)
            
            if dmdt < stopping_dmdt:
                break
        
        if save_m_steps is not None:
            self.save_m()
            
        if save_vtk_steps is not None:
            self.save_vtk()


if __name__=='__main__':
    pass