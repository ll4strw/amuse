import numpy

from amuse.units import units

from amuse.units.quantities import is_quantity, value_in, to_quantity

from amuse.datamodel import UnstructuredGrid, StructuredGrid,StructuredBaseGrid

try:
  import matplotlib
  from matplotlib import tri
  matplotlib_available=True
except:
  matplotlib_available=False

class interpolating_2D_remapper(object):
    def __init__(self, source, target,axes_names="xy"):
        """ this class maps a source grid to a target grid using linear 
            interpolation on a triangulation generated by adding a 
            midpoint to every cell (source should be a structured grid) 
            and thus generating 4 triangles for each cell. Values of the 
            midpoints are averaged from the corners. 
        """
        if len(source.shape) !=2:
            raise Exception("source grid is not 2D")
        if not isinstance(source, StructuredBaseGrid):
            raise Exception("source grid is not instance of StructuredBaseGrid")

        self.source=source
        self.target=target
        self._axes_names=list(axes_names)
        self.generate_triangulation()

    def _generate_nodes(self,grid,attributes):

        Nx,Ny=grid.shape

        x,y=numpy.mgrid[0:Nx,0:Ny]
        x1,y1=numpy.mgrid[0:Nx-1,0:Ny-1]
                
        x_=x.flatten()
        y_=y.flatten()
        x1_=x1.flatten()
        y1_=y1.flatten()

        l1=Nx*Ny

        i=numpy.arange(Nx*Ny).reshape((Nx,Ny))
        i1=(numpy.arange((Nx-1)*(Ny-1))+l1).reshape((Nx-1,Ny-1))

      
        nodes=UnstructuredGrid(len(x_)+len(x1_))
        for name in attributes:
          values1=getattr(grid,name)[x_,y_]
          values2=getattr(grid,name)[x1_,y1_]+getattr(grid,name)[x1_+1,y1_]+\
                  getattr(grid,name)[x1_,y1_+1]+getattr(grid,name)[x1_+1,y1_+1]
          setattr(nodes[0], name, 0.*values1[0])
          setattr(nodes[:l1], name, 1.*values1)
          setattr(nodes[l1:], name, values2/4)
        return nodes

    def _generate_elements_and_boundaries(self,grid):
        Nx,Ny=grid.shape

        l1=Nx*Ny

        i=numpy.arange(Nx*Ny).reshape((Nx,Ny))
        i1=(numpy.arange((Nx-1)*(Ny-1))+l1).reshape((Nx-1,Ny-1))

        e1=numpy.zeros(((Nx-1)*(Ny-1),3),dtype='i')
        e2=numpy.zeros(((Nx-1)*(Ny-1),3),dtype='i')
        e3=numpy.zeros(((Nx-1)*(Ny-1),3),dtype='i')
        e4=numpy.zeros(((Nx-1)*(Ny-1),3),dtype='i')
                
        e1[:,0]=i[:-1,:-1].flatten()
        e1[:,1]=i[1:,:-1].flatten()
        e1[:,2]=i1[:,:].flatten()
        
        e2[:,0]=i[1:,:-1].flatten()
        e2[:,1]=i[1:,1:].flatten()
        e2[:,2]=i1[:,:].flatten()
        
        e3[:,0]=i[1:,1:].flatten()
        e3[:,1]=i[:-1,1:].flatten()
        e3[:,2]=i1[:,:].flatten()
        
        e4[:,0]=i[:-1,:-1].flatten()
        e4[:,1]=i1[:,:].flatten()
        e4[:,2]=i[:-1,1:].flatten()

        elements=numpy.zeros((4*(Nx-1)*(Ny-1),3),dtype='i8')
        elements[0::4,:]=e1
        elements[1::4,:]=e2
        elements[2::4,:]=e3
        elements[3::4,:]=e4
      
        boundaries=[xx.flatten() for xx in [i[:,0],i[-1,:],i[::-1,-1],i[0,::-1]] ]
      
        elem=UnstructuredGrid(len(elements))
        elem.nodes=elements

        return elem,boundaries

    def convert_grid_to_nodes_and_elements(self, grid, attributes=None):
        
        if attributes is None:
            attributes=grid.get_attribute_names_defined_in_store()

        nodes=self._generate_nodes(grid, attributes)
        elements,boundaries=self._generate_elements_and_boundaries(grid)
      
        return nodes,elements,boundaries

    def generate_triangulation(self):

        nodes,elements,boundaries=self.convert_grid_to_nodes_and_elements(self.source, self._axes_names)

        xpos=to_quantity(getattr(nodes,self._axes_names[0]))
        ypos=to_quantity(getattr(nodes,self._axes_names[1]))
        
        self._xpos_unit=xpos.unit
        xpos=xpos.number
        self._ypos_unit=ypos.unit
        ypos=ypos.number

        n1=elements.nodes[:,0]
        n2=elements.nodes[:,1]
        n3=elements.nodes[:,2]
        elem=numpy.column_stack((n1,n2,n3))

        self._triangulation=tri.Triangulation(xpos,ypos,elem)
        
    def sample(self, values, xpos, ypos):
        interpolator=tri.LinearTriInterpolator(self._triangulation,values)
        return interpolator(xpos,ypos)

    def forward_mapping(self, attributes):
        if attributes is None:
            attributes=grid.get_attribute_names_defined_in_store()
        
        source=self.source.empty_copy()
        channel1=self.source.new_channel_to(source)
        target=self.target.empty_copy()
        channel2=self.target.new_channel_to(target)
        channel3=target.new_channel_to(self.target)
        
        channel1.copy_attributes(attributes)
        channel2.copy_attributes(self._axes_names)
        
        nodes=self._generate_nodes(source,attributes)
                
        xpos=value_in( getattr(target,self._axes_names[0]), self._xpos_unit)
        ypos=value_in( getattr(target,self._axes_names[1]), self._ypos_unit)
                
        for attribute in attributes:
            values=to_quantity( getattr(nodes,attribute) ) 
            unit=values.unit
            values=values.number
            samples=self.sample(values,xpos,ypos)
            setattr(target, attribute, (samples if unit is units.none else (samples | unit)))

        channel3.copy_attributes(attributes)    


from amuse.datamodel.staggeredgrid import StaggeredGrid

class conservative_spherical_remapper(object):

    def __init__(self, source, target, axes_names=['lon', 'lat']):
        """ This class maps a source grid to a target grid using second-
            order conservative remapping by calling the re-implementation of
            SCRIP within CDO. The source grid should be a structured grid
            the target grid can be of any type. This class is able to deal
            with staggered grids for both source and target grid.
            Instantiating this class may take a while, as the remapping
            weights are being computed.
        """
        self.src_staggered = False
        self.source = source
        self.src_elements = source
        if type(source) is StaggeredGrid:
            self.src_staggered = True
            self.src_elements = source.elements
        if not type(self.src_elements) is StructuredGrid:
            raise Exception("Source grid should be of type StructuredGrid")

        self.tgt_staggered = False
        self.target = target
        self.tgt_elements = target
        if type(target) is StaggeredGrid:
            self.tgt_staggered = True
            self.tgt_elements = target.elements
    
        self._axes_names=list(axes_names)

        try:
            from omuse.community.cdo.interface import CDORemapper
        except:
            raise Exception("conservative spherical remapper requires omuse.community.cdo.interface")  

        self.cdo_remapper = CDORemapper(channel="sockets", redirection="none")
        self.cdo_remapper.parameters.src_grid = self.src_elements
        self.cdo_remapper.parameters.dst_grid = self.tgt_elements

        #force start of the computation of remapping weights
        self.cdo_remapper.commit_parameters()

    def _get_grid_copies_and_channel(self, source, target, attributes):
        source_copy=source.empty_copy()
        channel1=source.new_channel_to(source_copy)
        target_copy=target.empty_copy()
        channel2=target.new_channel_to(target_copy)
        channel3=target_copy.new_channel_to(target)

        channel1.copy_attributes(attributes)
        channel2.copy_attributes(self._axes_names)

        return source_copy, target_copy, channel3

    def forward_mapping(self, attributes):

        element_attributes = attributes
        node_attributes = []

        #if the grid is staggered split the list of attributes into node an element attributes
        if self.src_staggered:
            el_attr = self.source.elements.all_attributes()
            no_attr = self.source.nodes.all_attributes()
            element_attributes = set(el_attr).intersection(set(attributes))
            node_attributes = set(no_attr).intersection(set(attributes)).difference(element_attributes)

        self._forward_mapping_elements_to_elements(self.src_elements, self.tgt_elements, element_attributes)
        if len(node_attributes) > 0:
            self._forward_mapping_nodes_to_nodes(self.source, self.target, node_attributes)


    def _forward_mapping_elements_to_elements(self, source, target, attributes):

        #create in-memory copies of the grids and a channel to the target in-code grid
        source_copy, target_copy, channel3 = self._get_grid_copies_and_channel(source, target, attributes)

        #indices for interacting with CDORemapper
        index_i_src = range(source.size)         
        index_i_dst = range(target.size)         
       
        for attribute in attributes:
            #obtain source values and unit
            values=to_quantity( getattr(source_copy, attribute) )
            unit=values.unit
            values=numpy.array(values.number)

            #do the remapping
            self.cdo_remapper.set_src_grid_values(index_i_src, values.ravel(order='F'))
            self.cdo_remapper.perform_remap()
            result = self.cdo_remapper.get_dst_grid_values(index_i_dst).reshape(target.shape, order='F')

            #store result in copy target grid
            setattr(target_copy, attribute, (result if unit is units.none else (result | unit)))

        #push in-memory copy target grid to in-code storage grid
        channel3.copy_attributes(attributes)    


    def _forward_mapping_nodes_to_nodes(self, source, target, attributes):

        #create in-memory copies of the grids and a channel to the target in-code grid
        source_copy, target_copy, channel3 = self._get_grid_copies_and_channel(source.nodes, target.nodes, attributes)

        #indices for interacting with CDORemapper
        index_i_src = range(source.elements.size)
        index_i_dst = range(target.elements.size)
       
        for attribute in attributes:
            #obtain source values and unit
            values=to_quantity( getattr(source_copy, attribute) ) 
            unit=values.unit
            values=values.number
            if len(values.shape) > 1:
                values = numpy.swapaxes(values, 0, 1)

            #remap to elements within source grid
            values = source.map_nodes_to_elements(values)

            #do the remapping
            self.cdo_remapper.set_src_grid_values(index_i_src, values.flatten())
            self.cdo_remapper.perform_remap()
            result = self.cdo_remapper.get_dst_grid_values(index_i_dst)

            #remap to nodes within target grid
            result = target.map_elements_to_nodes(result)

            #store result in copy target grid
            setattr(target_copy, attribute, (result if unit is units.none else (result | unit)))

        #push in-memory copy target grid to in-code storage target grid
        channel3.copy_attributes(attributes)




